from __future__ import annotations

from datetime import date
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .billing import numeric
from .const import DOMAIN
from .coordinator import NibaCoordinator

ENERGY = SensorDeviceClass.ENERGY
MONEY = SensorDeviceClass.MONETARY
POWER = SensorDeviceClass.POWER
TOTAL = SensorStateClass.TOTAL
MEASUREMENT = SensorStateClass.MEASUREMENT
DIAGNOSTIC = EntityCategory.DIAGNOSTIC
KWH = UnitOfEnergy.KILO_WATT_HOUR
KW = UnitOfPower.KILO_WATT

# Entidades retiradas de las versiones anteriores.
# Solo se eliminan de la entrada de configuración de Niba actual.
RETIRED_UNIQUE_IDS = frozenset(
    {
        "niba_usuario_nombre",
        "niba_usuario_apellidos",
        "niba_usuario_estado",
        "niba_usuario_email",
        "niba_usuario_codigo_miembro",
        "niba_usuario_email_validado",
        "niba_contrato_numero",
        "niba_contrato_cups",
        "niba_contrato_direccion",
        "niba_movements",
        "niba_sva_assistances",
        "niba_cases",
        "niba_products_catalog",
        "niba_cau",
        "niba_self_consumption_code",
        "niba_appliances_consumption_data",
        "niba_cups_technical_code",
        "niba_datadis_status",
        "niba_documents",
        "niba_invitations",
        "niba_consumption_powers_data",
        "niba_consumption_monthly_data",
        "niba_spending_rules",
        "niba_payment_cards",
    }
)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def number_value(value: Any) -> float | None:
    return numeric(value)


def amount_value(value: Any) -> float | None:
    if isinstance(value, list):
        value = value[0] if value else None

    parsed = numeric(value)
    return round(parsed, 2) if parsed is not None else None


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def rows(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []

    return [
        item
        for item in value
        if isinstance(item, dict)
    ]


def block(coordinator: NibaCoordinator, key: str) -> dict:
    return mapping(mapping(coordinator.data).get(key))


def consumption(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "consumption")


def api_consumption(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "consumption_api")


def curve_estimate(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "invoice_estimate")


def calculated(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "calculated_invoice")


def accrued(coordinator: NibaCoordinator) -> dict:
    return mapping(calculated(coordinator).get("acumulado"))


def forecast(coordinator: NibaCoordinator) -> dict:
    return mapping(calculated(coordinator).get("prevision"))


def calculation_attributes(coordinator: NibaCoordinator) -> dict:
    data = calculated(coordinator)
    return {key: value for key, value in data.items() if key != "ultimo_dia"}


def daily_rows(coordinator: NibaCoordinator) -> list[dict]:
    data = block(coordinator, "consumption_daily")

    return sorted(
        rows(data.get("daily_consumption")),
        key=lambda row: str(row.get("date") or ""),
    )


def latest_daily(coordinator: NibaCoordinator) -> dict:
    valid = [
        row
        for row in daily_rows(coordinator)
        if row.get("date")
    ]

    return valid[-1] if valid else {}


def measurements(coordinator: NibaCoordinator) -> list[dict]:
    return rows(
        latest_daily(coordinator).get("hour_measurements")
    )


def latest_hour(coordinator: NibaCoordinator) -> dict:
    valid = [
        row
        for row in measurements(coordinator)
        if row.get("hour") is not None
    ]

    def hour_key(row):
        value = row.get("hour")

        if isinstance(value, (int, float)):
            return f"{int(value):02d}:00"

        text = str(value)

        if text.isdigit():
            return f"{int(text):02d}:00"

        return text.zfill(5)

    return max(valid, key=hour_key) if valid else {}


def latest_monthly(coordinator: NibaCoordinator) -> dict:
    data = mapping(coordinator.data).get("consumption_monthly")

    if isinstance(data, dict):
        return data

    valid = rows(data)

    return max(
        valid,
        key=lambda row: str(row.get("date") or ""),
    ) if valid else {}


def latest_bill(coordinator: NibaCoordinator) -> dict:
    bill = block(coordinator, "latest_bill")

    if bill:
        return bill

    valid = rows(mapping(coordinator.data).get("bills"))

    return max(
        valid,
        key=lambda row: (
            str(row.get("end_at") or ""),
            str(row.get("contab_date") or ""),
        ),
    ) if valid else {}


def contract(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "contract")


def product(coordinator: NibaCoordinator) -> dict:
    contracts = rows(
        mapping(coordinator.data).get("contracts_v2")
    )

    if contracts:
        return mapping(contracts[0].get("product"))

    return mapping(contract(coordinator).get("product"))


def self_consumption(coordinator: NibaCoordinator) -> dict:
    return mapping(
        product(coordinator).get("self_consumption")
    )


def technical(coordinator: NibaCoordinator) -> dict:
    return block(coordinator, "cups_info")


def contracted_power(
    coordinator: NibaCoordinator,
    key: str,
) -> dict:
    return next(
        (
            row
            for row in rows(product(coordinator).get("powers"))
            if row.get("key") == key
        ),
        {},
    )


def inclusive_days(data: dict) -> int | None:
    start = parse_date(data.get("start_at"))
    end = parse_date(data.get("end_at"))

    if start is None or end is None or end < start:
        return None

    return (end - start).days + 1


def period_days(coordinator: NibaCoordinator) -> int | None:
    return inclusive_days(consumption(coordinator))


def data_days(coordinator: NibaCoordinator) -> int:
    data = consumption(coordinator)
    start = parse_date(data.get("start_at"))
    end = parse_date(data.get("end_at"))
    dates = set()

    for row in daily_rows(coordinator):
        day = parse_date(row.get("date"))

        if day is None:
            continue

        if start is not None and day < start:
            continue

        if end is not None and day > end:
            continue

        dates.add(day)

    return len(dates)


def status_text(value: Any) -> str | None:
    if value is None:
        return None

    return {
        "PENDING": "Pendiente",
        "PAID": "Pagada",
        "PAYMENT_PENDING": "Pendiente de pago",
        "CANCELLED": "Anulada",
    }.get(str(value).upper(), str(value))


def bill_taxes(bill: dict) -> float | None:
    vat = amount_value(bill.get("tax_amount"))
    electricity = amount_value(bill.get("nm_amount_ie"))

    if vat is None or electricity is None:
        return None

    return round(vat + electricity, 2)


def bill_social_charge(bill: dict) -> float | None:
    values = [
        amount_value(bill.get(key))
        for key in (
            "base_amount",
            "nm_term_ener",
            "term_power",
            "self_consumption_discount",
            "nm_amount_ie",
            "nm_rental_amount",
        )
    ]

    if any(value is None for value in values):
        return None

    base, energy, power, discount, tax, rental = values

    return round(
        base - energy - power - discount - tax - rental,
        2,
    )


def bill_period(coordinator: NibaCoordinator) -> str | None:
    bill = latest_bill(coordinator)
    start = bill.get("start_at")
    end = bill.get("end_at")

    return f"{start} → {end}" if start and end else None


# ---------------------------------------------------------
# ATRIBUTOS SELECCIONADOS
# ---------------------------------------------------------

def bill_attributes(coordinator: NibaCoordinator) -> dict:
    bill = latest_bill(coordinator)

    if not bill:
        return {}

    return {
        "fecha_emision": bill.get("contab_date"),
        "inicio": bill.get("start_at"),
        "fin": bill.get("end_at"),
        "dias": inclusive_days(bill),
        "estado": status_text(bill.get("status")),
        "consumo_kwh": number_value(
            bill.get("act_total_consumption")
        ),
        "total_eur": amount_value(bill.get("total_amount")),
        "energia_eur": amount_value(bill.get("nm_term_ener")),
        "potencia_eur": amount_value(bill.get("term_power")),
        "excedentes_facturados_kwh": number_value(
            bill.get("self_consumption_surplus")
        ),
        "excedentes_medidos_kwh": number_value(
            bill.get("total_surplus")
        ),
        "compensacion_eur": amount_value(
            bill.get("self_consumption_discount")
        ),
        "alquiler_eur": amount_value(
            bill.get("nm_rental_amount")
        ),
        "financiacion_bono_social_eur": bill_social_charge(bill),
        "impuesto_electrico_eur": amount_value(
            bill.get("nm_amount_ie")
        ),
        "iva_eur": amount_value(bill.get("tax_amount")),
        "impuestos_totales_eur": bill_taxes(bill),
        "base_iva_eur": amount_value(bill.get("base_amount")),
        "potencia_contratada_p1_kw": number_value(
            bill.get("contract_pwr_p1")
        ),
        "potencia_contratada_p2_kw": number_value(
            bill.get("contract_pwr_p2")
        ),
    }


def api_attributes(coordinator: NibaCoordinator) -> dict:
    data = api_consumption(coordinator)

    return {
        "fuente": "Resumen de consumo de Niba",
        "inicio_periodo": data.get("start_at"),
        "fin_periodo": data.get("end_at"),
        "ultimo_dato_api": data.get("date_last_data"),
        "consumo_api_kwh": number_value(
            data.get("consumption_value")
        ),
        "coste_api_eur": amount_value(
            data.get("consumption_amount")
        ),
        "consumo_estimado_api_kwh": number_value(
            data.get("estimated_consumption_value")
        ),
        "coste_estimado_api_eur": amount_value(
            data.get("estimated_consumption_amount")
        ),
        "incluye_total_factura": None,
        "interpretacion_importe": "Importe de Niba sin desglose verificado",
    }


def period_attributes(coordinator: NibaCoordinator) -> dict:
    data = consumption(coordinator)
    api = api_consumption(coordinator)
    diagnostic = block(coordinator, "daily_diagnostics")

    return {
        "inicio_periodo": data.get("start_at"),
        "fin_periodo_inclusivo": data.get("end_at"),
        "ultimo_dato": data.get("date_last_data"),
        "dias_periodo": period_days(coordinator),
        "dias_con_datos": data_days(coordinator),
        "coste_energia_resumen_api_eur": amount_value(
            api.get("consumption_amount")
        ),
        "consumo_medio_api_kwh": number_value(
            data.get("average_value")
        ),
        "coste_medio_api_eur": amount_value(
            data.get("average_amount")
        ),
        "consumo_estimado_kwh": number_value(
            data.get("estimated_consumption_value")
        ),
        "coste_energia_estimado_api_eur": amount_value(
            api.get("estimated_consumption_amount")
        ),
        "fuente_valor": "curvas_diarias",
        "fuente_coste_periodo": "curvas horarias y precios del contrato",
        "hasta_fecha_coste": calculated(coordinator).get("hasta_fecha"),
        "fecha_coste_periodo_api": api.get("date_last_data"),
        "fecha_resumen_api": api.get("date_last_data"),
        "consumo_periodo_api_kwh": number_value(
            api.get("consumption_value")
        ),
        "consumo_estimado_api_kwh": number_value(
            api.get("estimated_consumption_value")
        ),
        "diagnostico_curvas": {
            key: diagnostic.get(key)
            for key in (
                "estado",
                "fuente",
                "dias_disponibles",
                "hasta_fecha",
                "resumen_api_hasta",
            )
        },
    }


def estimate_attributes(coordinator: NibaCoordinator) -> dict:
    data = curve_estimate(coordinator)

    return {
        "fuente": "Proyección a partir de curvas diarias cerradas",
        "hasta_fecha_curvas": data.get("hasta_fecha"),
        "dias_con_curvas": data.get("dias_con_datos"),
        "dias_periodo": data.get("dias_periodo"),
        "metodo": data.get("metodo"),
        "consumo_estimado_api_kwh": number_value(
            api_consumption(coordinator).get(
                "estimated_consumption_value"
            )
        ),
        "ultimo_dato_api": api_consumption(coordinator).get(
            "date_last_data"
        ),
    }


def daily_attributes(coordinator: NibaCoordinator) -> dict:
    daily = daily_rows(coordinator)

    return {
        "fecha": latest_daily(coordinator).get("date"),
        "dias_disponibles": len(daily),
        "historico": [
            {
                "fecha": day.get("date"),
                "importacion_kwh": number_value(
                    day.get("total_active_value")
                ),
                "exportacion_kwh": number_value(
                    day.get("total_out_active_value")
                ),
                "reactiva": number_value(
                    day.get("total_reactive_value")
                ),
                "coste": amount_value(
                    day.get("daily_consumption_amount")
                ),
            }
            for day in daily
        ],
    }


def hourly_attributes(
    coordinator: NibaCoordinator,
    field: str,
) -> dict:
    return {
        "fecha": latest_daily(coordinator).get("date"),
        "medidas_horarias": [
            {
                "hora": row.get("hour"),
                "kwh": number_value(row.get(field)),
            }
            for row in measurements(coordinator)
        ],
    }


def profile_attributes(coordinator: NibaCoordinator) -> dict:
    return {
        "fecha": latest_daily(coordinator).get("date"),
        "medidas": [
            {
                "hour": row.get("hour"),
                "active_value": number_value(
                    row.get("active_value")
                ),
                "out_active_value": number_value(
                    row.get("out_active_value")
                ),
                "reactive_value": number_value(
                    row.get("reactive_value")
                ),
            }
            for row in measurements(coordinator)
        ],
    }


# ---------------------------------------------------------
# CÁLCULOS
# ---------------------------------------------------------

def average_consumption(
    coordinator: NibaCoordinator,
) -> float | None:
    value = number_value(
        consumption(coordinator).get("consumption_value")
    )
    days = data_days(coordinator)

    return (
        round(value / days, 2)
        if value is not None and days
        else None
    )


def progress(coordinator: NibaCoordinator) -> float | None:
    total = period_days(coordinator)

    return round(
        min(data_days(coordinator) / total * 100, 100),
        1,
    ) if total else None


def projection_values(coordinator: NibaCoordinator) -> dict:
    current = number_value(
        consumption(coordinator).get(
            "estimated_consumption_value"
        )
    )
    bill = latest_bill(coordinator)
    previous = number_value(
        bill.get("act_total_consumption")
    )
    current_days = period_days(coordinator)
    previous_days = inclusive_days(bill)

    if (
        current is None
        or previous is None
        or previous <= 0
        or not current_days
        or not previous_days
    ):
        return {}

    current_daily = current / current_days
    previous_daily = previous / previous_days

    return {
        "porcentaje": round(
            (current_daily - previous_daily)
            / previous_daily
            * 100,
            1,
        ),
        "proyeccion_actual_kwh": current,
        "dias_periodo_actual": current_days,
        "proyeccion_actual_kwh_dia": round(current_daily, 2),
        "factura_anterior_kwh": previous,
        "dias_factura_anterior": previous_days,
        "factura_anterior_kwh_dia": round(previous_daily, 2),
        "periodo_actual_inicio": consumption(coordinator).get(
            "start_at"
        ),
        "factura_anterior_inicio": bill.get("start_at"),
        "factura_anterior_fin": bill.get("end_at"),
        "metodo": "comparación de medias diarias normalizadas",
    }


def watts_to_kw(value: Any) -> float | None:
    value = number_value(value)

    return (
        round(value / 1000, 3)
        if value is not None
        else None
    )


# ---------------------------------------------------------
# SENSOR BASE
# ---------------------------------------------------------

class NibaSensor(CoordinatorEntity, SensorEntity):
    """Sensor with explicitly selected value and attributes."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: NibaCoordinator,
        name: str,
        unique_id: str,
        value_fn: Callable,
        *,
        unit=None,
        device_class=None,
        state_class=None,
        category=None,
        attributes_fn=None,
    ) -> None:
        super().__init__(coordinator)

        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_category = category
        self._value_fn = value_fn
        self._attributes_fn = attributes_fn

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, self.coordinator.entry.entry_id)
            },
            "name": "Niba energía",
            "manufacturer": "Niba",
            "model": "Niba API",
        }

    @property
    def native_value(self):
        value = self._value_fn(self.coordinator)

        if isinstance(value, (dict, list, tuple)):
            return None

        if (
            self._attr_native_unit_of_measurement is not None
            or self._attr_state_class is not None
            or self._attr_device_class in (
                ENERGY,
                MONEY,
                POWER,
            )
        ):
            return number_value(value)

        return value

    @property
    def extra_state_attributes(self):
        if self._attributes_fn is None:
            return {}

        return self._attributes_fn(self.coordinator)


# ---------------------------------------------------------
# SETUP
# ---------------------------------------------------------

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: NibaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    def add(name, uid, value_fn, **kwargs):
        entities.append(
            NibaSensor(
                coordinator,
                name,
                uid,
                value_fn,
                **kwargs,
            )
        )

    def field(
        name,
        uid,
        source,
        key,
        *,
        transform=None,
        **kwargs,
    ):
        def value_fn(c):
            value = source(c).get(key)
            return transform(value) if transform else value

        add(name, uid, value_fn, **kwargs)

    # -----------------------------------------------------
    # PERIODO ACTUAL
    # -----------------------------------------------------

    field(
        "Niba Consumo periodo",
        "niba_consumption_period",
        consumption,
        "consumption_value",
        unit=KWH,
        device_class=ENERGY,
        state_class=TOTAL,
        attributes_fn=period_attributes,
    )

    field(
        "Niba Coste energía periodo",
        "niba_cost_period",
        accrued,
        "energia_eur",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
        attributes_fn=calculation_attributes,
    )

    field(
        "Niba Consumo estimado",
        "niba_estimated_consumption",
        consumption,
        "estimated_consumption_value",
        unit=KWH,
        device_class=ENERGY,
        state_class=TOTAL,
        attributes_fn=calculation_attributes,
    )

    # Conserva el unique_id anterior.
    # El importe es una estimación de energía, no de factura completa.
    field(
        "Niba Coste energía estimado",
        "niba_estimated_cost",
        forecast,
        "energia_eur",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
        attributes_fn=calculation_attributes,
    )

    field(
        "Niba Importe previsto resumen API",
        "niba_estimated_cost_api",
        api_consumption,
        "estimated_consumption_amount",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
        category=DIAGNOSTIC,
        attributes_fn=api_attributes,
    )

    for name, uid, key in (
        (
            "Niba Consumo periodo resumen",
            "niba_consumption_period_api",
            "consumption_value",
        ),
        (
            "Niba Consumo estimado resumen",
            "niba_consumption_estimated_api",
            "estimated_consumption_value",
        ),
    ):
        field(
            name,
            uid,
            api_consumption,
            key,
            unit=KWH,
            device_class=ENERGY,
            state_class=TOTAL,
            category=DIAGNOSTIC,
            attributes_fn=api_attributes,
        )

    add(
        "Niba Consumo medio diario",
        "niba_average_consumption",
        average_consumption,
        unit=KWH,
        device_class=ENERGY,
        attributes_fn=lambda c: {
            "dias_con_datos": data_days(c),
            "hasta_fecha": latest_daily(c).get("date"),
            "metodo": "consumo periodo / días con curvas disponibles",
        },
    )

    field(
        "Niba Coste medio diario de energía",
        "niba_average_cost",
        accrued,
        "coste_medio_diario_eur",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
        attributes_fn=calculation_attributes,
    )

    for name, uid, key in (
        (
            "Niba Último dato",
            "niba_last_data",
            "date_last_data",
        ),
        (
            "Niba Inicio periodo",
            "niba_period_start",
            "start_at",
        ),
        (
            "Niba Fin periodo",
            "niba_period_end",
            "end_at",
        ),
        (
            "Niba Comparación periodo anterior",
            "niba_previous_period_comparison",
            "previous_period_comparison",
        ),
    ):
        field(
            name,
            uid,
            consumption,
            key,
            category=DIAGNOSTIC,
        )

    add(
        "Niba Días periodo",
        "niba_period_total_days",
        period_days,
        unit="días",
        category=DIAGNOSTIC,
    )

    add(
        "Niba Días con datos",
        "niba_period_data_days",
        data_days,
        unit="días",
        category=DIAGNOSTIC,
    )

    add(
        "Niba Progreso periodo",
        "niba_period_progress",
        progress,
        unit="%",
        state_class=MEASUREMENT,
    )

    # Totals use new unique IDs: historical energy sensors never become bill totals.
    for name, uid, source, key in (
        ("Niba Factura acumulada calculada", "niba_invoice_accrued", accrued, "total_eur"),
        ("Niba Factura prevista calculada", "niba_invoice_forecast", forecast, "total_eur"),
        ("Niba Potencia acumulada calculada", "niba_power_accrued_cost", accrued, "potencia_eur"),
        ("Niba Compensación calculada", "niba_surplus_accrued_credit", accrued, "compensacion_eur"),
        ("Niba Alquiler acumulado", "niba_rental_accrued", accrued, "alquiler_eur"),
        ("Niba Bono social acumulado", "niba_social_accrued", accrued, "bono_social_eur"),
        ("Niba Impuesto eléctrico acumulado", "niba_electricity_tax_accrued", accrued, "impuesto_electrico_eur"),
        ("Niba IVA acumulado", "niba_vat_accrued", accrued, "iva_eur"),
        ("Niba Importe resumen", "niba_cost_period_api", api_consumption, "consumption_amount"),
    ):
        field(name, uid, source, key, transform=amount_value,
              unit=CURRENCY_EURO, device_class=MONEY,
              category=DIAGNOSTIC if source is api_consumption else None,
              attributes_fn=api_attributes if source is api_consumption else calculation_attributes)

    field("Niba Consumo valorado", "niba_priced_consumption", calculated, "consumo_kwh",
          unit=KWH, device_class=ENERGY, state_class=TOTAL, attributes_fn=calculation_attributes)
    field("Niba Días contabilizados", "niba_priced_days", calculated, "dias_valorados",
          unit="días", attributes_fn=calculation_attributes)
    field("Niba Contabilizado hasta", "niba_priced_until", calculated, "hasta_fecha",
          attributes_fn=calculation_attributes)
    field("Niba Estado cálculo factura", "niba_invoice_calculation_status", calculated, "estado",
          category=DIAGNOSTIC, attributes_fn=calculation_attributes)
    field("Niba Estado previsión factura", "niba_invoice_forecast_status", calculated, "estado_prevision",
          category=DIAGNOSTIC, attributes_fn=calculation_attributes)
    for band in ("punta", "llano", "valle"):
        field(f"Niba Consumo valorado {band}", f"niba_priced_consumption_{band}",
              lambda c: mapping(calculated(c).get("consumo_por_periodo_kwh")), band,
              unit=KWH, device_class=ENERGY, state_class=TOTAL,
              attributes_fn=lambda c: {"hasta_fecha": calculated(c).get("hasta_fecha")})
        field(f"Niba Precio contratado {band}", f"niba_invoice_price_{band}",
              lambda c: mapping(calculated(c).get("tarifa")), "energia_" + band,
              unit="€/kWh", category=DIAGNOSTIC,
              attributes_fn=lambda c: {"factura_referencia_fin": mapping(calculated(c).get("tarifa")).get("fin_factura"),
                                       "fuente": "Contrato contrastado con última factura; importe provisional"})

    # -----------------------------------------------------
    # FACTURA ANTERIOR Y COMPARATIVA
    # -----------------------------------------------------

    field(
        "Niba Consumo factura anterior",
        "niba_previous_period_consumption",
        latest_bill,
        "act_total_consumption",
        unit=KWH,
        device_class=ENERGY,
        state_class=TOTAL,
    )

    field(
        "Niba Total factura anterior",
        "niba_previous_period_cost",
        latest_bill,
        "total_amount",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
    )

    add(
        "Niba Proyección vs factura anterior",
        "niba_projection_vs_previous",
        lambda c: projection_values(c).get("porcentaje"),
        unit="%",
        state_class=MEASUREMENT,
        attributes_fn=projection_values,
    )

    add(
        "Niba Factura anterior periodo",
        "niba_previous_period_info",
        bill_period,
        category=DIAGNOSTIC,
        attributes_fn=bill_attributes,
    )

    add(
        "Niba Última factura periodo",
        "niba_latest_bill_period",
        bill_period,
        attributes_fn=lambda c: {
            "inicio": latest_bill(c).get("start_at"),
            "fin": latest_bill(c).get("end_at"),
            "dias": inclusive_days(latest_bill(c)),
        },
    )

    # -----------------------------------------------------
    # ÚLTIMA FACTURA
    # -----------------------------------------------------

    for name, uid, key in (
        (
            "Niba Última factura total",
            "niba_latest_bill_total",
            "total_amount",
        ),
        (
            "Niba Última factura energía",
            "niba_latest_bill_energy",
            "nm_term_ener",
        ),
        (
            "Niba Última factura potencia",
            "niba_latest_bill_power",
            "term_power",
        ),
        (
            "Niba Última factura compensación",
            "niba_latest_bill_discount",
            "self_consumption_discount",
        ),
        (
            "Niba Última factura alquiler",
            "niba_latest_bill_rental",
            "nm_rental_amount",
        ),
        (
            "Niba Última factura impuesto eléctrico",
            "niba_latest_bill_electricity_tax",
            "nm_amount_ie",
        ),
        (
            "Niba Última factura IVA",
            "niba_latest_bill_vat",
            "tax_amount",
        ),
    ):
        field(
            name,
            uid,
            latest_bill,
            key,
            transform=amount_value,
            unit=CURRENCY_EURO,
            device_class=MONEY,
            attributes_fn=(
                bill_attributes
                if uid == "niba_latest_bill_total"
                else None
            ),
        )

    for name, uid, key in (
        (
            "Niba Última factura consumo",
            "niba_latest_bill_consumption",
            "act_total_consumption",
        ),
        (
            "Niba Última factura excedentes facturados",
            "niba_latest_bill_surplus",
            "self_consumption_surplus",
        ),
    ):
        field(
            name,
            uid,
            latest_bill,
            key,
            unit=KWH,
            device_class=ENERGY,
        )

    add(
        "Niba Última factura impuestos",
        "niba_latest_bill_taxes",
        lambda c: bill_taxes(latest_bill(c)),
        unit=CURRENCY_EURO,
        device_class=MONEY,
    )

    add(
        "Niba Última factura financiación bono social",
        "niba_latest_bill_social_charge",
        lambda c: bill_social_charge(latest_bill(c)),
        unit=CURRENCY_EURO,
        device_class=MONEY,
        attributes_fn=lambda c: {
            "metodo": (
                "calculado a partir de la base imponible "
                "de la factura"
            ),
        },
    )

    field(
        "Niba Última factura estado",
        "niba_latest_bill_status",
        latest_bill,
        "status",
        transform=status_text,
        attributes_fn=lambda c: {
            "fecha_emision": latest_bill(c).get("contab_date"),
        },
    )

    add(
        "Niba Facturas",
        "niba_bills",
        lambda c: len(
            rows(mapping(c.data).get("bills"))
        ),
        attributes_fn=lambda c: {
            "items": [
                {
                    "inicio": bill.get("start_at"),
                    "fin": bill.get("end_at"),
                    "fecha_emision": bill.get("contab_date"),
                    "consumo_kwh": number_value(
                        bill.get("act_total_consumption")
                    ),
                    "total_eur": amount_value(
                        bill.get("total_amount")
                    ),
                    "estado": status_text(
                        bill.get("status")
                    ),
                }
                for bill in rows(
                    mapping(c.data).get("bills")
                )
            ],
        },
    )

    # -----------------------------------------------------
    # ENERGÍA DIARIA Y HORARIA
    # -----------------------------------------------------

    field(
        "Niba Consumo diario",
        "niba_consumo_diario",
        latest_daily,
        "total_active_value",
        unit=KWH,
        device_class=ENERGY,
        state_class=TOTAL,
        attributes_fn=daily_attributes,
    )

    for name, uid, daily_key, hourly_key in (
        (
            "Niba Importación red último día",
            "niba_grid_import",
            "total_active_value",
            "active_value",
        ),
        (
            "Niba Exportación red último día",
            "niba_grid_export",
            "total_out_active_value",
            "out_active_value",
        ),
    ):
        field(
            name,
            uid,
            latest_daily,
            daily_key,
            unit=KWH,
            device_class=ENERGY,
            state_class=TOTAL,
            attributes_fn=(
                lambda c, key=hourly_key: hourly_attributes(c, key)
            ),
        )

    field(
        "Niba Energía reactiva diaria",
        "niba_daily_reactive",
        latest_daily,
        "total_reactive_value",
        unit="kVArh",
        state_class=MEASUREMENT,
        category=DIAGNOSTIC,
    )

    field(
        "Niba Coste diario",
        "niba_daily_cost",
        lambda c: mapping(calculated(c).get("ultimo_dia")),
        "energia_eur",
        transform=amount_value,
        unit=CURRENCY_EURO,
        device_class=MONEY,
        category=DIAGNOSTIC,
        attributes_fn=lambda c: {"fecha": calculated(c).get("hasta_fecha"),
                                  "fuente": "Curvas horarias y precios del contrato"},
    )

    add(
        "Niba Perfil horario",
        "niba_hourly_profile",
        lambda c: len(measurements(c)),
        category=DIAGNOSTIC,
        attributes_fn=profile_attributes,
    )

    field(
        "Niba Fecha última curva",
        "niba_latest_energy_date",
        latest_daily,
        "date",
        category=DIAGNOSTIC,
    )

    field(
        "Niba Última hora medida",
        "niba_latest_energy_hour",
        latest_hour,
        "hour",
        category=DIAGNOSTIC,
    )

    for name, uid, key in (
        (
            "Niba Importación última hora",
            "niba_latest_hour_import",
            "active_value",
        ),
        (
            "Niba Exportación última hora",
            "niba_latest_hour_export",
            "out_active_value",
        ),
    ):
        field(
            name,
            uid,
            latest_hour,
            key,
            unit=KWH,
            device_class=ENERGY,
            state_class=TOTAL,
            attributes_fn=lambda c: {
                "fecha": latest_daily(c).get("date"),
                "hora": latest_hour(c).get("hour"),
            },
        )

    # -----------------------------------------------------
    # CONSUMO MENSUAL
    # -----------------------------------------------------

    for name, uid, key, state, category in (
        (
            "Niba Consumo mensual",
            "niba_monthly_consumption_value",
            "consumption_value",
            TOTAL,
            None,
        ),
        (
            "Niba Consumo mensual estimado",
            "niba_monthly_estimated_consumption",
            "estimated_consumption_value",
            TOTAL,
            None,
        ),
        (
            "Niba Media diaria mensual",
            "niba_monthly_daily_average",
            "consumption_day_avg",
            None,
            None,
        ),
        (
            "Niba Autoconsumo mensual",
            "niba_monthly_self_consumption",
            "self_consumption_energy",
            TOTAL,
            DIAGNOSTIC,
        ),
    ):
        field(
            name,
            uid,
            latest_monthly,
            key,
            unit=KWH,
            device_class=ENERGY,
            state_class=state,
            category=category,
            attributes_fn=lambda c: {
                "fecha": latest_monthly(c).get("date"),
                "fuente": "Niba",
            },
        )

    # -----------------------------------------------------
    # SALDOS
    # Sin movimientos ni métodos de pago.
    # -----------------------------------------------------

    for name, uid, key, state in (
        (
            "Niba Saldo disponible",
            "niba_balance",
            "amount",
            None,
        ),
        (
            "Niba Saldo pendiente",
            "niba_pending_balance",
            "pending_amount",
            None,
        ),
        (
            "Niba Total cargado",
            "niba_total_loaded",
            "total_loaded",
            TOTAL,
        ),
        (
            "Niba Total gastado",
            "niba_total_spent",
            "total_spent",
            TOTAL,
        ),
        (
            "Niba Solar Battery",
            "niba_solar_battery",
            "solar_battery",
            None,
        ),
    ):
        field(
            name,
            uid,
            lambda c: block(c, "balances"),
            key,
            transform=amount_value,
            unit=CURRENCY_EURO,
            device_class=MONEY,
            state_class=state,
        )

    # -----------------------------------------------------
    # CONTRATO Y TARIFA
    # Sin nombre, correo, dirección, número de contrato o CUPS.
    # -----------------------------------------------------

    for name, uid, key in (
        (
            "Niba Contrato estado",
            "niba_contrato_estado",
            "status",
        ),
        (
            "Niba Contrato inicio",
            "niba_contrato_inicio",
            "start_at",
        ),
        (
            "Niba Contrato fin",
            "niba_contrato_fin",
            "end_at",
        ),
    ):
        field(
            name,
            uid,
            contract,
            key,
        )

    add(
        "Niba Contrato producto",
        "niba_contrato_producto",
        lambda c: (
            product(c).get("description")
            or product(c).get("name")
            or product(c).get("code")
        ),
    )

    for name, uid, key, category in (
        (
            "Niba Producto código",
            "niba_product_code",
            "code",
            None,
        ),
        (
            "Niba Producto nombre",
            "niba_product_name",
            "name",
            None,
        ),
        (
            "Niba Producto descripción",
            "niba_product_description",
            "description",
            None,
        ),
        (
            "Niba Producto familia",
            "niba_product_family",
            "family",
            DIAGNOSTIC,
        ),
        (
            "Niba Tarifa ATR",
            "niba_atr_tariff",
            "atr_tariff",
            DIAGNOSTIC,
        ),
    ):
        field(
            name,
            uid,
            product,
            key,
            category=category,
        )

    for period, uid in (
        ("P1", "niba_power_p1"),
        ("P2", "niba_power_p2"),
    ):
        add(
            f"Niba Potencia {period}",
            uid,
            lambda c, p=period: contracted_power(c, p).get("value"),
            unit=KW,
            device_class=POWER,
            attributes_fn=lambda c, p=period: {
                "periodo": p,
            },
        )

    # -----------------------------------------------------
    # AUTOCONSUMO
    # Sin CAU ni códigos identificativos del suministro.
    # -----------------------------------------------------

    for name, uid, key, unit, device, category in (
        (
            "Niba Precio excedentes",
            "niba_surplus_price",
            "usage_cl_price",
            "€/kWh",
            None,
            None,
        ),
        (
            "Niba Precio excedentes base",
            "niba_surplus_base_price",
            "usage_base_price",
            "€/kWh",
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Capacidad generación",
            "niba_generation_capacity",
            "generation_capacity",
            KW,
            POWER,
            None,
        ),
        (
            "Niba Tipo autoconsumo",
            "niba_self_consumption_type",
            "self_consumption_type",
            None,
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Modalidad compensación",
            "niba_self_consumption_payment_type",
            "payment_type",
            None,
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Tipo CUPS autoconsumo",
            "niba_self_consumption_cups_type",
            "cups_type",
            None,
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Tipo instalación",
            "niba_installation_type",
            "installation_type",
            None,
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Subtipo autoconsumo",
            "niba_self_consumption_subsection_type",
            "subsection_type",
            None,
            None,
            DIAGNOSTIC,
        ),
        (
            "Niba Inicio autoconsumo",
            "niba_self_consumption_start",
            "start_at",
            None,
            None,
            DIAGNOSTIC,
        ),
    ):
        field(
            name,
            uid,
            self_consumption,
            key,
            unit=unit,
            device_class=device,
            category=category,
        )

    # -----------------------------------------------------
    # INFORMACIÓN TÉCNICA
    # -----------------------------------------------------

    for name, uid, key in (
        (
            "Niba Tarifa ATR técnica",
            "niba_cups_atr_code",
            "codigo_tarifa_atr_en_vigor",
        ),
        (
            "Niba Perfil consumo",
            "niba_consumption_profile",
            "tipo_perfil_consumo",
        ),
        (
            "Niba CNAE",
            "niba_cnae",
            "cnae",
        ),
        (
            "Niba Sistema medida",
            "niba_metering_system",
            "system",
        ),
        (
            "Niba Última lectura distribuidora",
            "niba_last_meter_reading",
            "fecha_ultima_lectura",
        ),
    ):
        field(
            name,
            uid,
            technical,
            key,
            category=DIAGNOSTIC,
        )

    for name, uid, key in (
        (
            "Niba Potencia máxima BIEW",
            "niba_biew_max_power",
            "BIEW_max_power",
        ),
        (
            "Niba Potencia extensión",
            "niba_extension_power",
            "valor_derechos_extension_w",
        ),
        (
            "Niba Potencia técnica P1",
            "niba_cups_power_p1",
            "potencias_contratadas_en_wp1",
        ),
        (
            "Niba Potencia técnica P2",
            "niba_cups_power_p2",
            "potencias_contratadas_en_wp2",
        ),
    ):
        field(
            name,
            uid,
            technical,
            key,
            transform=watts_to_kw,
            unit=KW,
            device_class=POWER,
            category=DIAGNOSTIC,
        )

    # -----------------------------------------------------
    # LIMPIEZA DEL REGISTRO DE ENTIDADES ANTIGUAS
    #
    # Limitada a:
    # - Esta entrada de configuración.
    # - La plataforma Niba.
    # - El dominio sensor.
    # - Los 24 unique_id retirados.
    #
    # No elimina el historial de Recorder.
    # -----------------------------------------------------

    registry = er.async_get(hass)

    registered_entities = list(
        er.async_entries_for_config_entry(
            registry,
            entry.entry_id,
        )
    )

    for registered in registered_entities:
        if (
            registered.platform == DOMAIN
            and registered.domain == "sensor"
            and registered.unique_id in RETIRED_UNIQUE_IDS
        ):
            registry.async_remove(registered.entity_id)

    async_add_entities(entities)
    
