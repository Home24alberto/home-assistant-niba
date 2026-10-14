from __future__ import annotations

from datetime import date, timedelta
import logging

from .billing import month_bounds, estimate, as_date, numeric, period_matches

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
from .const import UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


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
        self._daily_month = None

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
        parsed = numeric(value)
        return round(parsed, 2) if parsed is not None else None

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
            if not isinstance(user, dict):
                user = {}

            # -------------------------------------------------
            # CONTRATOS
            # -------------------------------------------------
            contracts = await self.api.get_contracts()

            if not isinstance(contracts, list):
                contracts = []
            contracts = [item for item in contracts if isinstance(item, dict)]
            if not contracts:
                raise UpdateFailed(
                    "Niba no devuelve contratos"
                )

            self.contract = contracts[0]

            cups = self.contract.get("cups")

            if not cups:
                raise UpdateFailed(
                    "Niba no devuelve CUPS"
                )

            today = dt_util.now().date()
            period_start, period_end = month_bounds(
                today, self.contract.get("start_at")
            )
            last_data = min(today, period_end)
            month_key = (cups, period_start.isoformat())
            if month_key != self._daily_month:
                self._last_daily = []
                self._daily_month = month_key

            # -------------------------------------------------
            # CONTRATOS V2
            # -------------------------------------------------
            contracts_v2 = await self.api.get_contracts_v2()

            contracts_v2 = (
                [item for item in contracts_v2 if isinstance(item, dict)]
                if isinstance(contracts_v2, list)
                else []
            )

            # -------------------------------------------------
            # INFORMACIÓN TÉCNICA DEL CUPS
            # -------------------------------------------------
            cups_info = await self.api.get_cups_info(cups)

            if not isinstance(cups_info, dict):
                cups_info = {}

            # -------------------------------------------------
            # PERIODO ACTUAL
            # -------------------------------------------------
            consumption_response = await self.api.get_consumption_period(
                cups
            )

            if isinstance(consumption_response, dict) and consumption_response:
                self._last_consumption = consumption_response
                consumption = consumption_response
            else:
                cached = self._last_consumption
                # A temporary endpoint outage must not carry the prior
                # month's API totals and costs into a new billing period.
                cache_matches_period = period_matches(
                    cached, period_start, period_end
                )
                consumption = cached if cache_matches_period else {}

            # -------------------------------------------------
            # RANGO DE FECHAS
            # -------------------------------------------------
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

            daily_request_error = self.api.last_optional_error
            daily_response_received = daily_raw is not None

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

            # Discard malformed, duplicate, future and previous-period readings.
            # Keep the last occurrence for a date in case Niba sends duplicates.
            valid_daily = {}
            for row in daily_consumption:
                if not isinstance(row, dict):
                    continue
                row_date = as_date(row.get("date"))
                if (
                    row_date is None
                    or row_date < period_start
                    or row_date > min(period_end, today - timedelta(days=1))
                ):
                    continue
                valid_daily[row_date] = row

            daily_consumption = [valid_daily[key] for key in sorted(valid_daily)]
            self._last_daily = daily_consumption
            consumption_daily = {"daily_consumption": daily_consumption}
            if daily_response_received:
                daily_source = "api"
                daily_status = "datos_recibidos" if daily_consumption else "sin_curvas"
            elif daily_consumption:
                daily_source = "cache_memoria"
                daily_status = "api_no_disponible_cache"
            else:
                daily_source = "sin_datos"
                daily_status = "api_no_disponible_sin_cache"
            daily_diagnostics = {
                "estado": daily_status,
                "fuente": daily_source,
                "dias_disponibles": len(daily_consumption),
                "hasta_fecha": daily_consumption[-1].get("date") if daily_consumption else None,
                "resumen_api_hasta": consumption.get("date_last_data"),
                "error_api": daily_request_error,
            }

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

            invoice_estimate = estimate(
                daily_consumption, period_start, period_end, today
            )
            invoice_estimate["motivo_factura"] = (
                "La API consultada no proporciona el total de la factura; "
                "consulta Niba Estimación energía API para el coste de energía"
            )
            # All displayed period summaries use the same measured days.
            consumption_api = consumption
            consumption = dict(consumption_api)
            consumption.update({
                "start_at": period_start.isoformat(), "end_at": period_end.isoformat(),
                "date_last_data": invoice_estimate.get("hasta_fecha"),
                "consumption_value": invoice_estimate.get("consumo_medido_kwh"),
                "consumption_amount": consumption_api.get("consumption_amount"),
                "estimated_consumption_value": invoice_estimate.get("consumo_estimado_kwh"),
                "estimated_consumption_amount": consumption_api.get("estimated_consumption_amount"),
                "api_date_last_data": consumption_api.get("date_last_data"),
                "api_consumption_value": consumption_api.get("consumption_value"),
                "api_estimated_consumption_value": consumption_api.get("estimated_consumption_value"),
            })

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
                "daily_diagnostics": daily_diagnostics,
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
