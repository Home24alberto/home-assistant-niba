from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import NibaApiClient, NibaApiError, NibaAuthenticationError
from .billing import as_date, estimate, month_bounds, period_matches
from .contract_prices import ContractTableError, read_contract_pdf, reconcile_contract_invoice
from .const import UPDATE_INTERVAL
from .invoice import InvoiceError, estimate_invoice, parse_pdf

_LOGGER = logging.getLogger(__name__)

# Only explicitly selected fields may reach coordinator.data.
# CUPS is used locally to query Niba; it is not published as entity data.

PERIOD_FIELDS = (
    "start_at",
    "end_at",
    "date_last_data",
    "consumption_value",
    "consumption_amount",
    "average_value",
    "average_amount",
    "estimated_consumption_value",
    "estimated_consumption_amount",
    "previous_period_comparison",
)

DAY_FIELDS = (
    "date",
    "total_active_value",
    "total_out_active_value",
    "total_reactive_value",
    "daily_consumption_amount",
)

HOUR_FIELDS = (
    "hour",
    "active_value",
    "out_active_value",
    "reactive_value",
)

MONTH_FIELDS = (
    "date",
    "consumption_value",
    "estimated_consumption_value",
    "consumption_day_avg",
    "self_consumption_energy",
    "a_limit",
    "b_limit",
)

BILL_FIELDS = (
    "start_at",
    "end_at",
    "contab_date",
    "status",
    "act_total_consumption",
    "total_amount",
    "nm_term_ener",
    "term_power",
    "self_consumption_surplus",
    "total_surplus",
    "self_consumption_discount",
    "nm_rental_amount",
    "nm_amount_ie",
    "tax_amount",
    "base_amount",
    "contract_pwr_p1",
    "contract_pwr_p2",
)

PRODUCT_FIELDS = (
    "code",
    "name",
    "description",
    "family",
    "family_code",
    "atr_tariff",
    "min_power",
    "max_power",
)

SELF_CONSUMPTION_FIELDS = (
    "usage_cl_price",
    "usage_base_price",
    "generation_capacity",
    "self_consumption_type",
    "payment_type",
    "cups_type",
    "installation_type",
    "subsection_type",
    "start_at",
)

TECHNICAL_FIELDS = (
    "codigo_tarifa_atr_en_vigor",
    "tipo_perfil_consumo",
    "cnae",
    "system",
    "fecha_ultima_lectura",
    "BIEW_max_power",
    "valor_derechos_extension_w",
    "potencias_contratadas_en_wp1",
    "potencias_contratadas_en_wp2",
)

BALANCE_FIELDS = (
    "amount",
    "pending_amount",
    "total_loaded",
    "total_spent",
    "solar_battery",
)

ESTIMATE_FIELDS = (
    "hasta_fecha",
    "dias_con_datos",
    "dias_periodo",
    "metodo",
    "consumo_medido_kwh",
    "consumo_estimado_kwh",
)

# These API fields may contain [amount, currency].
AMOUNT_FIELDS = frozenset(
    (
        "consumption_amount",
        "average_amount",
        "estimated_consumption_amount",
        "daily_consumption_amount",
        "total_amount",
        "nm_term_ener",
        "term_power",
        "self_consumption_discount",
        "nm_rental_amount",
        "nm_amount_ie",
        "tax_amount",
        "base_amount",
        *BALANCE_FIELDS,
    )
)


def select_fields(data: Any, fields: tuple[str, ...]) -> dict:
    """Copy allowed scalar fields, never arbitrary nested API content."""
    if not isinstance(data, dict):
        return {}

    result = {}

    for key in fields:
        value = data.get(key)

        if key in AMOUNT_FIELDS and isinstance(value, list):
            value = value[0] if value else None

        if value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value

    return result


def safe_product(value: Any) -> dict:
    """Retain tariff details without customer or installation identifiers."""
    if not isinstance(value, dict):
        return {}

    result = select_fields(value, PRODUCT_FIELDS)

    powers = value.get("powers")
    result["powers"] = [
        select_fields(power, ("key", "value", "description"))
        for power in powers
        if isinstance(power, dict)
    ] if isinstance(powers, list) else []

    result["self_consumption"] = select_fields(
        value.get("self_consumption"),
        SELF_CONSUMPTION_FIELDS,
    )

    return result


def safe_contract(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}

    result = select_fields(value, ("status", "start_at", "end_at"))
    result["product"] = safe_product(value.get("product"))
    return result


def safe_day(value: dict) -> dict:
    result = select_fields(value, DAY_FIELDS)
    measurements = value.get("hour_measurements")
    result["hour_measurements"] = [
        select_fields(row, HOUR_FIELDS)
        for row in measurements
        if isinstance(row, dict)
    ] if isinstance(measurements, list) else []
    return result


class NibaCoordinator(DataUpdateCoordinator):
    """Retrieve only data needed by the energy integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self.entry = entry
        self.email = entry.data["email"]
        self.password = entry.data["password"]

        self.api = NibaApiClient(
            async_get_clientsession(hass),
            self.email,
            self.password,
        )

        # This public attribute contains a sanitized contract only.
        self.contract: dict | None = None
        self._last_consumption: dict = {}
        self._last_daily: list[dict] = []
        self._last_bills: list[dict] = []
        self._daily_month = None
        self._selected_cups = None
        self._invoice_key = None
        self._tariff = None
        self._tariff_status = "sin_factura"
        self._invoice_retry = None
        self._contract_price_key = None
        self._contract_prices = None
        self._contract_price_retry = None
        self._contract_price_status = "contrato_sin_precios_verificados"

        super().__init__(
            hass,
            logger=_LOGGER,
            name="Niba",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    @staticmethod
    def _bill_sort_key(bill: dict) -> tuple[str, str]:
        return (
            str(bill.get("end_at") or ""),
            str(bill.get("contab_date") or ""),
        )

    async def _async_update_data(self):
        try:
            # Retrieve energy data and documents used to validate contracted prices.
            contracts_raw = await self.api.get_contracts()
            contracts = [
                item
                for item in contracts_raw
                if isinstance(item, dict)
            ] if isinstance(contracts_raw, list) else []

            if not contracts:
                raise UpdateFailed("Niba no devuelve contratos")

            active = [c for c in contracts if str(c.get("status", "")).casefold() == "active"]
            if len(active) > 1:
                raise UpdateFailed("Varios suministros activos: selección automática no disponible")
            selected_contract = active[0] if active else contracts[0]
            cups = selected_contract.get("cups")

            if not isinstance(cups, str) or not cups:
                raise UpdateFailed("Niba no devuelve el suministro")

            if cups != self._selected_cups:
                self._last_consumption = {}
                self._last_daily = []
                self._last_bills = []
                self._daily_month = None
                self._selected_cups = cups
                self._invoice_key = None
                self._tariff = None
                self._tariff_status = "sin_factura"
                self._invoice_retry = None

            self.contract = safe_contract(selected_contract)

            today = dt_util.now().astimezone(ZoneInfo("Europe/Madrid")).date()
            period_start, period_end = month_bounds(
                today, selected_contract.get("start_at")
            )
            last_data = min(today, period_end)
            year_start = date(today.year, 1, 1)

            month_key = (cups, period_start.isoformat())
            if month_key != self._daily_month:
                self._last_daily = []
                self._daily_month = month_key

            contracts_v2_raw = await self.api.get_contracts_v2()
            candidates = [
                item
                for item in contracts_v2_raw
                if isinstance(item, dict)
            ] if isinstance(contracts_v2_raw, list) else []

            # Match the selected supply when Niba supplies this field.
            matching = [
                item for item in candidates
                if item.get("cups") == cups
            ]
            if matching:
                selected_v2 = matching[0]
            elif (
                len(contracts) == 1
                and len(candidates) == 1
                and not candidates[0].get("cups")
            ):
                selected_v2 = candidates[0]
            else:
                selected_v2 = {}

            contracts_v2 = (
                [safe_contract(selected_v2)] if selected_v2 else []
            )

            cups_info_raw = await self.api.get_cups_info(cups)
            if isinstance(cups_info_raw, dict):
                technical = cups_info_raw.get("cups")
                if not isinstance(technical, dict):
                    technical = cups_info_raw
            else:
                technical = {}

            cups_info = select_fields(technical, TECHNICAL_FIELDS)

            consumption_response = await self.api.get_consumption_period(cups)

            if isinstance(consumption_response, dict) and consumption_response:
                consumption_api = select_fields(
                    consumption_response, PERIOD_FIELDS
                )
                self._last_consumption = consumption_api
            else:
                cached = self._last_consumption
                consumption_api = (
                    dict(cached)
                    if period_matches(cached, period_start, period_end)
                    else {}
                )

            daily_raw = await self.api.get_consumption_daily(
                cups,
                period_start.isoformat(),
                last_data.isoformat(),
            )

            if isinstance(daily_raw, list):
                received_daily = daily_raw
                daily_response_received = True
            elif (
                isinstance(daily_raw, dict)
                and isinstance(daily_raw.get("daily_consumption"), list)
            ):
                received_daily = daily_raw["daily_consumption"]
                daily_response_received = True
            else:
                received_daily = self._last_daily
                daily_response_received = False

            valid_daily = {}
            latest_allowed = min(
                period_end, today - timedelta(days=1)
            )

            for row in received_daily:
                if not isinstance(row, dict):
                    continue

                row_date = as_date(row.get("date"))
                if (
                    row_date is None
                    or row_date < period_start
                    or row_date > latest_allowed
                ):
                    continue

                valid_daily[row_date] = safe_day(row)

            daily_consumption = [
                valid_daily[key] for key in sorted(valid_daily)
            ]
            self._last_daily = daily_consumption

            if daily_response_received:
                daily_source = "api"
                daily_status = (
                    "datos_recibidos" if daily_consumption else "sin_curvas"
                )
            elif daily_consumption:
                daily_source = "cache_memoria"
                daily_status = "api_no_disponible_cache"
            else:
                daily_source = "sin_datos"
                daily_status = "api_no_disponible_sin_cache"

            # Do not publish raw API errors, URLs or response bodies.
            daily_diagnostics = {
                "estado": daily_status,
                "fuente": daily_source,
                "dias_disponibles": len(daily_consumption),
                "hasta_fecha": (
                    daily_consumption[-1].get("date")
                    if daily_consumption else None
                ),
                "resumen_api_hasta": consumption_api.get("date_last_data"),
            }

            monthly_raw = await self.api.get_consumption_monthly(
                cups,
                year_start.isoformat(),
                last_data.isoformat(),
            )

            if isinstance(monthly_raw, list):
                consumption_monthly = [
                    select_fields(row, MONTH_FIELDS)
                    for row in monthly_raw
                    if isinstance(row, dict)
                ]
            elif isinstance(monthly_raw, dict):
                consumption_monthly = select_fields(
                    monthly_raw, MONTH_FIELDS
                )
            else:
                consumption_monthly = []

            balances = select_fields(
                await self.api.get_balances(),
                BALANCE_FIELDS,
            )

            bills_raw = await self.api.get_bills(cups)

            if isinstance(bills_raw, list):
                bills = [
                    select_fields(bill, BILL_FIELDS)
                    for bill in bills_raw
                    if isinstance(bill, dict)
                ]
                bills.sort(key=self._bill_sort_key)

                if bills:
                    self._last_bills = bills
                else:
                    bills = self._last_bills
            else:
                bills = self._last_bills

            latest_bill = bills[-1] if bills else {}
            raw_bills = [b for b in bills_raw if isinstance(b, dict)] if isinstance(bills_raw, list) else []
            raw_bills.sort(key=self._bill_sort_key)
            raw_latest = raw_bills[-1] if raw_bills else None
            if raw_latest:
                key = (raw_latest.get("document_id"), raw_latest.get("end_at"),
                       str(raw_latest.get("total_amount")), raw_latest.get("status"))
                now = dt_util.now()
                changed = key != self._invoice_key
                if changed or (self._tariff is None and (self._invoice_retry is None or now >= self._invoice_retry)):
                    self._invoice_key = key
                    self._tariff = None
                    self._invoice_retry = now + timedelta(hours=6)
                    if not isinstance(key[0], str) or not key[0]:
                        self._tariff_status = "factura_sin_documento"
                    elif str(raw_latest.get("status", "")).upper() in ("CANCELLED", "ANULADA"):
                        self._tariff_status = "factura_anulada"
                    else:
                        try:
                            pdf = await self.api.get_invoice_pdf(key[0])
                            self._tariff = await self.hass.async_add_executor_job(parse_pdf, pdf, raw_latest)
                            self._tariff_status = "factura_validada"
                        except NibaAuthenticationError:
                            raise
                        except NibaApiError:
                            self._tariff_status = "descarga_factura_no_disponible"
                        except InvoiceError as err:
                            self._tariff_status = str(err)
            calculation_contract = dict(selected_v2 or selected_contract)
            document_id = calculation_contract.get("document_id") or selected_contract.get("document_id")
            contract_key = (cups, document_id, calculation_contract.get("start_at"),
                            calculation_contract.get("end_at"))
            now = dt_util.now()
            if contract_key != self._contract_price_key or self._contract_price_retry is None or now >= self._contract_price_retry:
                self._contract_price_key = contract_key
                self._contract_prices = None
                self._contract_price_retry = now + timedelta(hours=6)
                self._contract_price_status = "contrato_sin_documento"
                if isinstance(document_id, str) and document_id:
                    try:
                        contract_pdf = await self.api.get_invoice_pdf(document_id)
                        self._contract_prices = await self.hass.async_add_executor_job(read_contract_pdf, contract_pdf)
                        self._contract_price_status = "contrato_leido"
                    except NibaAuthenticationError:
                        raise
                    except NibaApiError:
                        self._contract_price_status = "descarga_contrato_no_disponible"
                    except ContractTableError as err:
                        self._contract_price_status = str(err)
            effective_tariff = None
            effective_status = self._tariff_status
            if self._tariff is not None:
                effective_status = self._contract_price_status
                if self._contract_prices is not None:
                    try:
                        effective_tariff = reconcile_contract_invoice(self._contract_prices, self._tariff)
                        effective_status = "contrato_y_factura_coinciden"
                    except ContractTableError as err:
                        effective_status = str(err)

            # Address stays private. The first supported version uses mainland/Balearic schedules and IVA.
            address = selected_contract.get("address") or {}
            postal = str(address.get("postal") or "") if isinstance(address, dict) else ""
            calculated = estimate_invoice(daily_consumption, period_start, period_end, today,
                                          effective_tariff, calculation_contract)
            if not effective_tariff:
                calculated["estado"] = effective_status
            if not (len(postal) == 5 and postal.isdigit() and 1 <= int(postal[:2]) <= 50
                    and postal[:2] not in ("35", "38")):
                calculated = {"estado": "territorio_no_validado", "provisional": True}
            calculated["estado_tarifa"] = effective_status
            calculated["fuente_precios"] = (
                "contrato contrastado con ultima factura" if effective_tariff else "sin_validar"
            )
            calculated["fuente_otros_cargos"] = "ultima factura; referencia provisional"
            calculated["fuente_curvas"] = daily_source

            # Keep measured kWh and the legacy arithmetic diagnostic separately.
            # Primary forecasts come from the validated hourly calculation.
            invoice_estimate = select_fields(
                estimate(
                    daily_consumption,
                    period_start,
                    period_end,
                    today,
                ),
                ESTIMATE_FIELDS,
            )

            consumption = dict(consumption_api)
            consumption.update(
                {
                    "start_at": period_start.isoformat(),
                    "end_at": period_end.isoformat(),
                    "date_last_data": invoice_estimate.get("hasta_fecha"),
                    "consumption_value": invoice_estimate.get(
                        "consumo_medido_kwh"
                    ),
                    "estimated_consumption_value": calculated.get("consumo_previsto_kwh"),
                    "api_date_last_data": consumption_api.get(
                        "date_last_data"
                    ),
                    "api_consumption_value": consumption_api.get(
                        "consumption_value"
                    ),
                    "api_estimated_consumption_value": consumption_api.get(
                        "estimated_consumption_value"
                    ),
                }
            )

            _LOGGER.debug(
                "Niba actualizado: curvas=%s, facturas=%s",
                len(daily_consumption),
                len(bills),
            )

            return {
                "contract": self.contract,
                "contracts_v2": contracts_v2,
                "cups_info": cups_info,
                "consumption": consumption,
                "consumption_api": consumption_api,
                "invoice_estimate": invoice_estimate,
                "calculated_invoice": calculated,
                "consumption_daily": {
                    "daily_consumption": daily_consumption,
                },
                "daily_diagnostics": daily_diagnostics,
                "consumption_monthly": consumption_monthly,
                "balances": balances,
                "bills": bills,
                "latest_bill": latest_bill,
                "date_range": {
                    "period_start": period_start.isoformat(),
                    "last_data": last_data.isoformat(),
                    "year_start": year_start.isoformat(),
                },
            }

        except NibaAuthenticationError:
            # Avoid attaching an API exception containing private details.
            raise ConfigEntryAuthFailed(
                "No se pudo autenticar con Niba"
            ) from None

        except NibaApiError:
            raise UpdateFailed(
                "No se pudieron actualizar los datos de Niba"
            ) from None
