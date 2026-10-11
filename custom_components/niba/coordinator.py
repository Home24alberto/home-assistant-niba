from __future__ import annotations

from datetime import date, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    NibaApiClient,
    NibaApiError,
    NibaAuthenticationError,
)
from .billing import as_date, consumption_summary, estimate, month_bounds
from .const import (
    CONF_ELECTRICITY_TAX,
    CONF_ENERGY_LLANO,
    CONF_ENERGY_PUNTA,
    CONF_ENERGY_VALLE,
    CONF_INVOICE_ESTIMATE,
    CONF_POWER_P1_DAY,
    CONF_POWER_P1_KW,
    CONF_POWER_P2_DAY,
    CONF_POWER_P2_KW,
    CONF_RENTAL_DAY,
    CONF_SOCIAL_DAY,
    CONF_SURPLUS_KWH,
    CONF_VALLEY_DATES,
    CONF_VAT,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _billing_settings(options: dict) -> dict | None:
    """Build calculator settings exclusively from this user's options."""
    if not options.get(CONF_INVOICE_ESTIMATE, False):
        return None
    try:
        settings = {
            "energy_kwh": {
                "punta": float(options[CONF_ENERGY_PUNTA]),
                "llano": float(options[CONF_ENERGY_LLANO]),
                "valle": float(options[CONF_ENERGY_VALLE]),
            },
            "power_p1_kw": float(options[CONF_POWER_P1_KW]),
            "power_p2_kw": float(options[CONF_POWER_P2_KW]),
            "power_p1_day": float(options[CONF_POWER_P1_DAY]),
            "power_p2_day": float(options[CONF_POWER_P2_DAY]),
            "surplus_kwh": float(options.get(CONF_SURPLUS_KWH, 0)),
            "rental_day": float(options.get(CONF_RENTAL_DAY, 0)),
            "social_day": float(options.get(CONF_SOCIAL_DAY, 0)),
            "electricity_tax": (
                float(options.get(CONF_ELECTRICITY_TAX, 0)) / 100
            ),
            "vat": float(options.get(CONF_VAT, 0)) / 100,
            "valley_dates": {
                item.strip()
                for item in str(options.get(CONF_VALLEY_DATES, "")).split(",")
                if item.strip()
            },
        }
    except (KeyError, TypeError, ValueError):
        return None

    required = (
        *settings["energy_kwh"].values(),
        settings["power_p1_kw"],
        settings["power_p2_kw"],
        settings["power_p1_day"],
        settings["power_p2_day"],
    )
    return settings if all(value > 0 for value in required) else None


class NibaCoordinator(DataUpdateCoordinator):
    """Coordinator for Niba API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self.hass = hass
        self.entry = entry

        self.email = entry.data["email"]
        self.password = entry.data["password"]

        session = async_get_clientsession(hass)

        self.api = NibaApiClient(
            session,
            self.email,
            self.password,
        )

        self.contract: dict | None = None
        self._last_consumption: dict = {}
        self._last_daily: list = []
        self._last_bills: list = []
        self._daily_month: tuple[str, str] | None = None

        # Histórico persistente de periodos devueltos por
        # consumption-period. No equivale al histórico de facturas.
        # Se conserva por compatibilidad y diagnóstico.
        self._period_store = Store(
            hass,
            1,
            f"niba_period_history_{entry.entry_id}",
        )
        self._period_history_loaded = False
        self._period_history = {
            "active": None,
            "previous": None,
        }

        super().__init__(
            hass,
            logger=_LOGGER,
            name="Niba",
            update_interval=timedelta(
                seconds=UPDATE_INTERVAL
            ),
        )

    @staticmethod
    def _amount_number(value):
        """Return numeric amount from Niba [value, currency]."""
        if isinstance(value, list):
            value = value[0] if value else None

        if isinstance(value, (int, float)):
            return round(float(value), 2)

        return None

    @staticmethod
    def _bill_sort_key(bill: dict) -> tuple[str, str, str]:
        """Return a stable chronological key for a Niba bill."""
        return (
            str(bill.get("end_at") or ""),
            str(bill.get("contab_date") or ""),
            str(bill.get("id") or ""),
        )

    async def _async_update_period_history(
        self,
        consumption: dict,
        daily_consumption: list,
    ) -> dict:
        """Persist current and previous API consumption periods."""

        if not self._period_history_loaded:
            stored = await self._period_store.async_load()

            if isinstance(stored, dict):
                self._period_history = {
                    "active": stored.get("active"),
                    "previous": stored.get("previous"),
                }

            self._period_history_loaded = True

        daily_summary = []

        for day in daily_consumption:
            if not isinstance(day, dict):
                continue

            daily_summary.append(
                {
                    "date": day.get("date"),
                    "import_kwh": day.get("total_active_value"),
                    "export_kwh": day.get("total_out_active_value"),
                    "reactive": day.get("total_reactive_value"),
                }
            )

        snapshot = {
            "start_at": consumption.get("start_at"),
            "end_at": consumption.get("end_at"),
            "date_last_data": consumption.get("date_last_data"),
            "consumption_kwh": consumption.get("consumption_value"),
            "cost_eur": self._amount_number(
                consumption.get("consumption_amount")
            ),
            "average_kwh": consumption.get("average_value"),
            "average_cost_eur": self._amount_number(
                consumption.get("average_amount")
            ),
            "estimated_consumption_kwh": (
                consumption.get("estimated_consumption_value")
            ),
            "estimated_cost_eur": self._amount_number(
                consumption.get("estimated_consumption_amount")
            ),
            "previous_period_comparison": (
                consumption.get("previous_period_comparison")
            ),
            "daily": daily_summary,
        }

        active = self._period_history.get("active")

        current_start = snapshot.get("start_at")
        active_start = (
            active.get("start_at")
            if isinstance(active, dict)
            else None
        )

        changed = False

        if not isinstance(active, dict):
            self._period_history["active"] = snapshot
            changed = True

        elif (
            current_start
            and active_start
            and current_start != active_start
        ):
            # Niba ha comenzado un nuevo periodo de consumo API.
            self._period_history["previous"] = active
            self._period_history["active"] = snapshot
            changed = True

        elif active != snapshot:
            # Mismo periodo: actualizar último estado conocido.
            self._period_history["active"] = snapshot
            changed = True

        if changed:
            await self._period_store.async_save(
                self._period_history
            )

        return self._period_history

    async def _async_update_data(self):
        """Fetch all available Niba data."""

        try:
            # -------------------------------------------------
            # USUARIO
            # -------------------------------------------------
            user = await self.api.get_user()

            # -------------------------------------------------
            # CONTRATOS
            # -------------------------------------------------
            contracts = await self.api.get_contracts()

            if not isinstance(contracts, list) or not contracts:
                raise UpdateFailed(
                    "Niba no devuelve contratos"
                )

            self.contract = contracts[0]

            cups = self.contract.get("cups")

            if not cups:
                raise UpdateFailed(
                    "Niba no devuelve CUPS"
                )

            # -------------------------------------------------
            # CONTRATOS V2
            # -------------------------------------------------
            contracts_v2 = await self.api.get_contracts_v2()

            if not isinstance(contracts_v2, list):
                contracts_v2 = []

            # -------------------------------------------------
            # INFORMACIÓN TÉCNICA DEL CUPS
            # -------------------------------------------------
            cups_info = await self.api.get_cups_info(cups)

            if not isinstance(cups_info, dict):
                cups_info = {}

            # -------------------------------------------------
            # PERIODO ACTUAL
            # -------------------------------------------------
            consumption = await self.api.get_consumption_period(
                cups
            )

            if isinstance(consumption, dict) and consumption:
                self._last_consumption = consumption
            else:
                consumption = self._last_consumption

            if not isinstance(consumption, dict):
                consumption = {}

            # -------------------------------------------------
            # RANGO DE FECHAS
            # -------------------------------------------------
            today = dt_util.now().date()
            period_start, period_end = month_bounds(
                today, self.contract.get("start_at")
            )
            last_data = min(today, period_end)
            month_key = (cups, period_start.isoformat())
            if month_key != self._daily_month:
                self._last_daily = []
                self._daily_month = month_key

            # Para histórico mensual consultamos desde
            # el inicio del año.
            year_start = date(today.year, 1, 1)

            # -------------------------------------------------
            # CONSUMO DIARIO / HORARIO
            # -------------------------------------------------
            daily_raw = await self.api.get_consumption_daily(
                cups,
                period_start.isoformat(),
                last_data.isoformat(),
            )

            if isinstance(daily_raw, list):
                daily_consumption = daily_raw
                self._last_daily = daily_raw
            elif isinstance(daily_raw, dict):
                possible_daily = daily_raw.get(
                    "daily_consumption",
                    [],
                )

                daily_consumption = (
                    possible_daily
                    if isinstance(possible_daily, list)
                    else []
                )

                if daily_consumption:
                    self._last_daily = daily_consumption
            else:
                daily_consumption = self._last_daily

            # Never mix delayed/cached curves from another month or an open day.
            daily_consumption = [
                row
                for row in daily_consumption
                if isinstance(row, dict)
                and as_date(row.get("date")) is not None
                and period_start
                <= as_date(row.get("date"))
                <= min(period_end, today - timedelta(days=1))
            ]
            self._last_daily = daily_consumption
            consumption_daily = {"daily_consumption": daily_consumption}

            # -------------------------------------------------
            # HISTÓRICO DE PERIODOS DE CONSUMO API
            # -------------------------------------------------
            period_history = (
                await self._async_update_period_history(
                    consumption,
                    daily_consumption,
                )
            )

            # -------------------------------------------------
            # CONSUMO MENSUAL
            # -------------------------------------------------
            consumption_monthly = (
                await self.api.get_consumption_monthly(
                    cups,
                    year_start.isoformat(),
                    last_data.isoformat(),
                )
            )

            if not isinstance(
                consumption_monthly,
                (list, dict),
            ):
                consumption_monthly = []

            # -------------------------------------------------
            # POTENCIAS MEDIDAS
            # -------------------------------------------------
            consumption_powers = (
                await self.api.get_consumption_powers(
                    cups,
                    period_start.isoformat(),
                    last_data.isoformat(),
                )
            )

            if not isinstance(
                consumption_powers,
                (list, dict),
            ):
                consumption_powers = []

            # -------------------------------------------------
            # CONSUMO POR ELECTRODOMÉSTICOS
            # -------------------------------------------------
            appliances_consumption = (
                await self.api.get_appliances_consumption(
                    cups,
                    period_start.isoformat(),
                    last_data.isoformat(),
                )
            )

            if not isinstance(
                appliances_consumption,
                (list, dict),
            ):
                appliances_consumption = []

            # -------------------------------------------------
            # BALANCE / WALLET
            # -------------------------------------------------
            balances = await self.api.get_balances()

            if not isinstance(balances, dict):
                balances = {}

            movements = await self.api.get_movements()

            if not isinstance(movements, dict):
                movements = {}

            # -------------------------------------------------
            # FACTURAS
            # -------------------------------------------------
            bills_raw = await self.api.get_bills(cups)

            if isinstance(bills_raw, list):
                bills = [
                    bill for bill in bills_raw
                    if isinstance(bill, dict)
                ]
                bills.sort(key=self._bill_sort_key)

                if bills:
                    self._last_bills = bills
                elif self._last_bills:
                    bills = self._last_bills
            else:
                bills = self._last_bills

            latest_bill = bills[-1] if bills else {}

            # -------------------------------------------------
            # PRODUCTOS NIBA
            # -------------------------------------------------
            products = await self.api.get_products()

            if not isinstance(products, list):
                products = []

            # -------------------------------------------------
            # BLOQUES AUXILIARES
            # -------------------------------------------------
            spending_rules = (
                await self.api.get_spending_rules()
            )

            if not isinstance(spending_rules, list):
                spending_rules = []

            payment_cards = (
                await self.api.get_payment_cards()
            )

            if not isinstance(payment_cards, list):
                payment_cards = []

            cases = await self.api.get_cases()

            if not isinstance(cases, list):
                cases = []

            sva_assistances = (
                await self.api.get_sva_assistances()
            )

            if not isinstance(sva_assistances, list):
                sva_assistances = []

            invitations = await self.api.get_invitations()

            if not isinstance(invitations, list):
                invitations = []

            # -------------------------------------------------
            # DOCUMENTOS DEL CONTRATO
            # -------------------------------------------------
            documents = []

            if contracts_v2:
                contract_v2 = contracts_v2[0]

                document_refs = (
                    contract_v2.get("documents_url")
                    if isinstance(contract_v2, dict)
                    else []
                )

                if isinstance(document_refs, list):
                    for document_ref in document_refs:
                        if not isinstance(document_ref, dict):
                            continue

                        document_id = document_ref.get(
                            "document_id"
                        )

                        if not document_id:
                            continue

                        document_data = (
                            await self.api.get_document(
                                document_id
                            )
                        )

                        documents.append(
                            {
                                "document_id": document_id,
                                "document_type": (
                                    document_ref.get(
                                        "document_type"
                                    )
                                ),
                                "data": (
                                    document_data
                                    if isinstance(
                                        document_data,
                                        dict,
                                    )
                                    else {}
                                ),
                            }
                        )

            # -------------------------------------------------
            # RESUMEN COHERENTE Y PREVISIÓN DE FACTURA OPCIONAL
            # -------------------------------------------------
            consumption_api = consumption
            summary = consumption_summary(
                daily_consumption,
                period_start,
                period_end,
                today,
            )
            settings = _billing_settings(dict(self.entry.options))
            if settings is None:
                invoice_estimate = {
                    **summary,
                    "configurado": False,
                    "incluye_total_factura": True,
                    "provisional": True,
                    "motivo": (
                        "Configura tus precios y potencias en las opciones "
                        "de la integración"
                    ),
                }
            else:
                invoice_estimate = estimate(
                    daily_consumption,
                    period_start,
                    period_end,
                    today,
                    settings,
                    latest_bill,
                )

            # Period, measured days and consumption all use the same curves.
            consumption = dict(consumption_api)
            consumption.update(
                {
                    "start_at": period_start.isoformat(),
                    "end_at": period_end.isoformat(),
                    "date_last_data": summary.get("hasta_fecha"),
                    "consumption_value": summary.get("consumo_medido_kwh"),
                    "estimated_consumption_value": summary.get(
                        "consumo_estimado_kwh"
                    ),
                }
            )
            if settings is not None:
                consumption["consumption_amount"] = invoice_estimate.get(
                    "coste_energia_medido_eur"
                )
                consumption["estimated_consumption_amount"] = (
                    invoice_estimate.get("energia_eur")
                )

            _LOGGER.debug(
                "Niba actualizado. daily=%s, monthly=%s, bills=%s",
                len(daily_consumption),
                (
                    len(consumption_monthly)
                    if isinstance(
                        consumption_monthly,
                        list,
                    )
                    else 1
                ),
                len(bills),
            )

            return {
                # Compatibilidad con integración anterior
                "contract": self.contract,
                "cups": cups,
                "consumption": consumption,
                "consumption_api": consumption_api,
                "invoice_estimate": invoice_estimate,
                "consumption_daily": consumption_daily,
                "balances": balances,
                "user": user,
                "movements": movements,

                # Niba v2
                "contracts": contracts,
                "contracts_v2": contracts_v2,
                "cups_info": cups_info,
                "consumption_monthly": consumption_monthly,
                "consumption_powers": consumption_powers,
                "appliances_consumption": (
                    appliances_consumption
                ),
                "bills": bills,
                "latest_bill": latest_bill,
                "products": products,
                "spending_rules": spending_rules,
                "payment_cards": payment_cards,
                "cases": cases,
                "sva_assistances": sva_assistances,
                "invitations": invitations,
                "documents": documents,

                # Histórico de periodos del endpoint de consumo.
                # No debe utilizarse como histórico de facturas.
                "period_history": period_history,

                # Información interna útil
                "date_range": {
                    "period_start": (
                        period_start.isoformat()
                    ),
                    "last_data": last_data.isoformat(),
                    "year_start": year_start.isoformat(),
                },
            }

        except NibaAuthenticationError as err:
            raise ConfigEntryAuthFailed from err

        except NibaApiError as err:
            raise UpdateFailed("No se pudieron actualizar los datos de Niba") from err
