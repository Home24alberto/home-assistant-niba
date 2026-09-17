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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NibaCoordinator


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def amount_value(value: Any) -> Any:
    """Extract and normalize amount from Niba [value, currency]."""
    if isinstance(value, list):
        value = value[0] if value else None

    if isinstance(value, (int, float)):
        return round(float(value), 2)

    return value


def number_value(value: Any) -> float | None:
    """Return a numeric Niba value or None."""
    value = amount_value(value)

    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nested_value(data: Any, path: tuple[Any, ...]) -> Any:
    """Read a nested value safely."""
    current = data

    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list):
                return None
            if len(current) <= key:
                return None
            current = current[key]
            continue

        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def format_address(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None

    street = value.get("street") or ""
    number = value.get("street_number") or ""
    postal = value.get("postal") or ""
    city = value.get("city") or ""
    province = value.get("province") or ""

    parts = []

    first = f"{street} {number}".strip()
    if first:
        parts.append(first)

    second = f"{postal} {city}".strip()
    if second:
        parts.append(second)

    if province:
        parts.append(province)

    return ", ".join(parts) or None


def bool_text(value: Any) -> str | None:
    if value is None:
        return None
    return "Sí" if bool(value) else "No"


def bill_status_text(value: Any) -> str | None:
    statuses = {
        "PENDING": "Pendiente",
        "PAID": "Pagada",
        "PAYMENT_PENDING": "Pendiente de pago",
        "CANCELLED": "Anulada",
    }

    if value is None:
        return None

    return statuses.get(str(value).upper(), str(value))


def get_consumption(coordinator: NibaCoordinator) -> dict:
    value = coordinator.data.get("consumption", {})
    return value if isinstance(value, dict) else {}


def get_latest_bill(coordinator: NibaCoordinator) -> dict:
    value = coordinator.data.get("latest_bill", {})

    if isinstance(value, dict) and value:
        return value

    bills = coordinator.data.get("bills", [])

    if not isinstance(bills, list):
        return {}

    valid = [bill for bill in bills if isinstance(bill, dict)]

    if not valid:
        return {}

    return max(
        valid,
        key=lambda bill: (
            str(bill.get("end_at") or ""),
            str(bill.get("contab_date") or ""),
            str(bill.get("id") or ""),
        ),
    )


def get_latest_daily(coordinator: NibaCoordinator) -> dict:
    data = coordinator.data.get("consumption_daily", {})

    if not isinstance(data, dict):
        return {}

    daily = data.get("daily_consumption", [])

    if not isinstance(daily, list):
        return {}

    valid = [
        item
        for item in daily
        if isinstance(item, dict) and item.get("date")
    ]

    if not valid:
        return {}

    return max(valid, key=lambda item: str(item.get("date") or ""))


def get_latest_monthly(coordinator: NibaCoordinator) -> dict:
    """Return latest monthly consumption record."""
    data = coordinator.data.get("consumption_monthly", [])

    if isinstance(data, list):
        valid = [item for item in data if isinstance(item, dict)]

        if not valid:
            return {}

        return max(
            valid,
            key=lambda item: str(item.get("date") or ""),
        )

    if isinstance(data, dict):
        return data

    return {}


def get_contract_v2(coordinator: NibaCoordinator) -> dict:
    value = coordinator.data.get("contracts_v2", [])

    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            return value[0]

    return {}


def get_cups_object(coordinator: NibaCoordinator) -> dict:
    info = coordinator.data.get("cups_info", {})

    if not isinstance(info, dict):
        return {}

    cups = info.get("cups")

    if isinstance(cups, dict):
        return cups

    return info


def current_period_days(coordinator: NibaCoordinator) -> int | None:
    """Days in current period; Niba end_at is an exclusive boundary."""
    consumption = get_consumption(coordinator)
    start = parse_date(consumption.get("start_at"))
    end = parse_date(consumption.get("end_at"))

    if start is None or end is None or end <= start:
        return None

    return (end - start).days


def current_data_days(coordinator: NibaCoordinator) -> int | None:
    """Return the number of distinct daily curves in the current period.

    Niba's consumption-period ``date_last_data`` can advance before the
    corresponding daily curves are published.  Counting the actual records
    avoids understating the daily averages and overstating period progress.
    """
    consumption = get_consumption(coordinator)
    start = parse_date(consumption.get("start_at"))
    end = parse_date(consumption.get("end_at"))

    daily_data = coordinator.data.get("consumption_daily", {})
    daily = (
        daily_data.get("daily_consumption", [])
        if isinstance(daily_data, dict)
        else []
    )

    curve_dates: set[date] = set()

    if isinstance(daily, list):
        for item in daily:
            if not isinstance(item, dict):
                continue

            curve_date = parse_date(item.get("date"))

            if curve_date is None:
                continue

            if start is not None and curve_date < start:
                continue

            if end is not None and curve_date >= end:
                continue

            curve_dates.add(curve_date)

    if curve_dates:
        return len(curve_dates)

    # Fallback for a temporary failure of the optional daily endpoint.
    last_data = parse_date(consumption.get("date_last_data"))

    if start is None or last_data is None or last_data < start:
        return None

    return (last_data - start).days + 1


def bill_days(bill: dict) -> int | None:
    """Days in an issued bill; both invoice dates are inclusive."""
    start = parse_date(bill.get("start_at"))
    end = parse_date(bill.get("end_at"))

    if start is None or end is None or end < start:
        return None

    return (end - start).days + 1


def bill_social_charge(bill: dict) -> float | None:
    """Derive the social-bonus financing charge from invoice totals."""
    base = number_value(bill.get("base_amount"))
    energy = number_value(bill.get("nm_term_ener"))
    power = number_value(bill.get("term_power"))
    discount = number_value(bill.get("self_consumption_discount"))
    electricity_tax = number_value(bill.get("nm_amount_ie"))
    rental = number_value(bill.get("nm_rental_amount"))

    values = (
        base,
        energy,
        power,
        discount,
        electricity_tax,
        rental,
    )

    if any(value is None for value in values):
        return None

    return round(
        base - (
            energy
            + power
            + discount
            + electricity_tax
            + rental
        ),
        2,
    )


def bill_total_taxes(bill: dict) -> float | None:
    vat = number_value(bill.get("tax_amount"))
    electricity_tax = number_value(bill.get("nm_amount_ie"))

    if vat is None or electricity_tax is None:
        return None

    return round(vat + electricity_tax, 2)


# ---------------------------------------------------------
# SETUP
# ---------------------------------------------------------

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: NibaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        # Entidades existentes: se conservan todos sus unique_id.
        NibaConsumptionPeriodSensor(coordinator),
        NibaCostPeriodSensor(coordinator),
        NibaEstimatedConsumptionSensor(coordinator),
        NibaEstimatedCostSensor(coordinator),
        NibaBalanceSensor(coordinator),
        NibaPendingBalanceSensor(coordinator),
        NibaTotalLoadedSensor(coordinator),
        NibaTotalSpentSensor(coordinator),
        NibaSolarBatterySensor(coordinator),
        NibaDailyConsumptionSensor(coordinator),
        NibaGridImportSensor(coordinator),
        NibaGridExportSensor(coordinator),

        NibaUserFieldSensor(
            coordinator, "Niba Usuario nombre", "niba_usuario_nombre", "name"
        ),
        NibaUserFieldSensor(
            coordinator,
            "Niba Usuario apellidos",
            "niba_usuario_apellidos",
            "last_name",
        ),
        NibaUserFieldSensor(
            coordinator, "Niba Usuario estado", "niba_usuario_estado", "status"
        ),
        NibaUserFieldSensor(
            coordinator, "Niba Usuario email", "niba_usuario_email", "email"
        ),
        NibaUserFieldSensor(
            coordinator,
            "Niba Usuario código de miembro",
            "niba_usuario_codigo_miembro",
            "member_code",
        ),
        NibaUserEmailValidatedSensor(coordinator),

        NibaContractFieldSensor(
            coordinator,
            "Niba Contrato número",
            "niba_contrato_numero",
            ("contract_number",),
        ),
        NibaContractFieldSensor(
            coordinator,
            "Niba Contrato CUPS",
            "niba_contrato_cups",
            ("cups",),
        ),
        NibaContractProductSensor(coordinator),
        NibaContractFieldSensor(
            coordinator,
            "Niba Contrato estado",
            "niba_contrato_estado",
            ("status",),
        ),
        NibaContractFieldSensor(
            coordinator,
            "Niba Contrato inicio",
            "niba_contrato_inicio",
            ("start_at",),
        ),
        NibaContractFieldSensor(
            coordinator,
            "Niba Contrato fin",
            "niba_contrato_fin",
            ("end_at",),
        ),
        NibaContractAddressSensor(coordinator),

        # Periodo actual y comparativa con la factura real anterior.
        NibaCurrentAverageConsumptionSensor(coordinator),
        NibaCurrentAverageCostSensor(coordinator),
        NibaPeriodFieldSensor(
            coordinator,
            "Niba Último dato",
            "niba_last_data",
            ("date_last_data",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaPeriodFieldSensor(
            coordinator,
            "Niba Inicio periodo",
            "niba_period_start",
            ("start_at",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaPeriodFieldSensor(
            coordinator,
            "Niba Fin periodo",
            "niba_period_end",
            ("end_at",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaPreviousPeriodConsumptionSensor(coordinator),
        NibaPreviousPeriodCostSensor(coordinator),
        NibaProjectionVsPreviousSensor(coordinator),
        NibaPreviousPeriodInfoSensor(coordinator),
        NibaPeriodFieldSensor(
            coordinator,
            "Niba Comparación periodo anterior API",
            "niba_previous_period_comparison",
            ("previous_period_comparison",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaPeriodTotalDaysSensor(coordinator),
        NibaPeriodDataDaysSensor(coordinator),
        NibaPeriodProgressSensor(coordinator),

        # Última factura emitida por Niba.
        NibaLatestBillTotalSensor(coordinator),
        NibaLatestBillPeriodSensor(coordinator),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura consumo",
            "niba_latest_bill_consumption",
            ("act_total_consumption",),
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura energía",
            "niba_latest_bill_energy",
            ("nm_term_ener",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura potencia",
            "niba_latest_bill_power",
            ("term_power",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura excedentes facturados",
            "niba_latest_bill_surplus",
            ("self_consumption_surplus",),
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura compensación",
            "niba_latest_bill_discount",
            ("self_consumption_discount",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura alquiler",
            "niba_latest_bill_rental",
            ("nm_rental_amount",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura impuesto eléctrico",
            "niba_latest_bill_electricity_tax",
            ("nm_amount_ie",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillFieldSensor(
            coordinator,
            "Niba Última factura IVA",
            "niba_latest_bill_vat",
            ("tax_amount",),
            unit=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            transform=amount_value,
        ),
        NibaLatestBillTaxesSensor(coordinator),
        NibaLatestBillSocialChargeSensor(coordinator),
        NibaLatestBillStatusSensor(coordinator),

        # Energía diaria y horaria.
        NibaDailyReactiveSensor(coordinator),
        NibaDailyCostSensor(coordinator),
        NibaHourlyProfileSensor(coordinator),
        NibaLatestEnergyDateSensor(coordinator),
        NibaLatestEnergyHourSensor(coordinator),
        NibaLatestHourImportSensor(coordinator),
        NibaLatestHourExportSensor(coordinator),
        NibaMonthlyConsumptionValueSensor(coordinator),
        NibaMonthlyEstimatedConsumptionSensor(coordinator),
        NibaMonthlyAverageDailySensor(coordinator),
        NibaMonthlySelfConsumptionSensor(coordinator),

        # Contrato y producto.
        NibaContractV2Sensor(
            coordinator,
            "Niba Producto código",
            "niba_product_code",
            ("product", "code"),
        ),
        NibaContractV2Sensor(
            coordinator,
            "Niba Producto nombre",
            "niba_product_name",
            ("product", "name"),
        ),
        NibaContractV2Sensor(
            coordinator,
            "Niba Producto descripción",
            "niba_product_description",
            ("product", "description"),
        ),
        NibaContractV2Sensor(
            coordinator,
            "Niba Producto familia",
            "niba_product_family",
            ("product", "family"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaContractV2Sensor(
            coordinator,
            "Niba Tarifa ATR",
            "niba_atr_tariff",
            ("product", "atr_tariff"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaPowerContractedSensor(
            coordinator, "P1", "Niba Potencia P1", "niba_power_p1"
        ),
        NibaPowerContractedSensor(
            coordinator, "P2", "Niba Potencia P2", "niba_power_p2"
        ),

        # Autoconsumo.
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Precio excedentes",
            "niba_surplus_price",
            ("usage_cl_price",),
            unit="€/kWh",
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Precio excedentes base",
            "niba_surplus_base_price",
            ("usage_base_price",),
            unit="€/kWh",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Capacidad generación",
            "niba_generation_capacity",
            ("generation_capacity",),
            unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Tipo autoconsumo",
            "niba_self_consumption_type",
            ("self_consumption_type",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Modalidad compensación",
            "niba_self_consumption_payment_type",
            ("payment_type",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba CAU",
            "niba_cau",
            ("cau",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Tipo CUPS autoconsumo",
            "niba_self_consumption_cups_type",
            ("cups_type",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Tipo instalación",
            "niba_installation_type",
            ("installation_type",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Subtipo autoconsumo",
            "niba_self_consumption_subsection_type",
            ("subsection_type",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaSelfConsumptionSensor(
            coordinator,
            "Niba Inicio autoconsumo",
            "niba_self_consumption_start",
            ("start_at",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),

        # Información técnica del CUPS.
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba CUPS técnico",
            "niba_cups_technical_code",
            ("cups",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Tarifa ATR técnica",
            "niba_cups_atr_code",
            ("codigo_tarifa_atr_en_vigor",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Perfil consumo",
            "niba_consumption_profile",
            ("tipo_perfil_consumo",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Código autoconsumo",
            "niba_self_consumption_code",
            ("self_consumption_code",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba CNAE",
            "niba_cnae",
            ("cnae",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Sistema medida",
            "niba_metering_system",
            ("system",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Última lectura distribuidora",
            "niba_last_meter_reading",
            ("fecha_ultima_lectura",),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Potencia máxima BIEW",
            "niba_biew_max_power",
            ("BIEW_max_power",),
            transform=NibaCupsTechnicalSensor.watts_to_kw,
            unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Potencia extensión",
            "niba_extension_power",
            ("valor_derechos_extension_w",),
            transform=NibaCupsTechnicalSensor.watts_to_kw,
            unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Potencia técnica P1",
            "niba_cups_power_p1",
            ("potencias_contratadas_en_wp1",),
            transform=NibaCupsTechnicalSensor.watts_to_kw,
            unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCupsTechnicalSensor(
            coordinator,
            "Niba Potencia técnica P2",
            "niba_cups_power_p2",
            ("potencias_contratadas_en_wp2",),
            transform=NibaCupsTechnicalSensor.watts_to_kw,
            unit=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaDatadisSensor(coordinator),

        # Bloques variables y de diagnóstico.
        NibaCollectionSensor(
            coordinator,
            "Niba Registros consumo mensual",
            "niba_consumption_monthly_data",
            "consumption_monthly",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Potencias medidas",
            "niba_consumption_powers_data",
            "consumption_powers",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Consumo electrodomésticos",
            "niba_appliances_consumption_data",
            "appliances_consumption",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator, "Niba Facturas", "niba_bills", "bills"
        ),
        NibaMovementsSensor(coordinator),
        NibaCollectionSensor(
            coordinator,
            "Niba Tarjetas de pago",
            "niba_payment_cards",
            "payment_cards",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Casos",
            "niba_cases",
            "cases",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Asistencias SVA",
            "niba_sva_assistances",
            "sva_assistances",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Invitaciones",
            "niba_invitations",
            "invitations",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaCollectionSensor(
            coordinator,
            "Niba Reglas de gasto",
            "niba_spending_rules",
            "spending_rules",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        NibaDocumentsSensor(coordinator),
        NibaProductsCatalogSensor(coordinator),
    ]

    async_add_entities(entities)


# ---------------------------------------------------------
# BASE
# ---------------------------------------------------------

class NibaBaseSensor(CoordinatorEntity, SensorEntity):
    """Base Niba sensor."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: NibaCoordinator,
        name: str,
        unique_id: str,
        *,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator)

        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_entity_category = entity_category

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, self.coordinator.config_entry.entry_id)
            },
            "name": "Niba energía",
            "manufacturer": "Niba",
            "model": "Niba API",
        }


class NibaGenericSensor(NibaBaseSensor):
    """Generic sensor reading coordinator data."""

    def __init__(
        self,
        coordinator: NibaCoordinator,
        name: str,
        unique_id: str,
        source: Callable[[NibaCoordinator], Any],
        path: tuple[Any, ...],
        *,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        entity_category: EntityCategory | None = None,
        transform: Callable[[Any], Any] | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            name,
            unique_id,
            entity_category=entity_category,
        )

        self._source = source
        self._path = path
        self._transform = transform
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def native_value(self):
        data = self._source(self.coordinator)
        value = nested_value(data, self._path)

        if self._transform:
            value = self._transform(value)

        if isinstance(value, (dict, list)):
            return None

        return value


# ---------------------------------------------------------
# PERIODO ACTUAL
# ---------------------------------------------------------

class NibaConsumptionPeriodSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo periodo",
            "niba_consumption_period",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_consumption(self.coordinator).get("consumption_value")

    @property
    def extra_state_attributes(self):
        data = get_consumption(self.coordinator)

        return {
            "cups": data.get("cups_electricity"),
            "inicio_periodo": data.get("start_at"),
            "fin_periodo_exclusivo": data.get("end_at"),
            "ultimo_dato": data.get("date_last_data"),
            "dias_periodo": current_period_days(self.coordinator),
            "dias_con_datos": current_data_days(self.coordinator),
            "coste_energia_periodo_eur": amount_value(
                data.get("consumption_amount")
            ),
            "consumo_medio_api_kwh": data.get("average_value"),
            "coste_medio_api_eur": amount_value(data.get("average_amount")),
            "consumo_estimado_kwh": data.get("estimated_consumption_value"),
            "coste_energia_estimado_eur": amount_value(
                data.get("estimated_consumption_amount")
            ),
            "comparacion_periodo_anterior_api": data.get(
                "previous_period_comparison"
            ),
        }


class NibaCostPeriodSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Coste energía periodo",
            "niba_cost_period",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return amount_value(
            get_consumption(self.coordinator).get("consumption_amount")
        )

    @property
    def extra_state_attributes(self):
        return {
            "tipo": "Término de energía proporcionado por Niba",
            "incluye_total_factura": False,
        }


class NibaEstimatedConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo estimado",
            "niba_estimated_consumption",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_consumption(self.coordinator).get(
            "estimated_consumption_value"
        )


class NibaEstimatedCostSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Coste energía estimado",
            "niba_estimated_cost",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return amount_value(
            get_consumption(self.coordinator).get(
                "estimated_consumption_amount"
            )
        )

    @property
    def extra_state_attributes(self):
        return {
            "tipo": "Estimación del término de energía de Niba",
            "incluye_total_factura": False,
        }


class NibaPeriodFieldSensor(NibaGenericSensor):
    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        super().__init__(
            coordinator,
            name,
            unique_id,
            get_consumption,
            path,
            **kwargs,
        )


class NibaCurrentAverageConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo medio diario",
            "niba_average_consumption",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = None

    @property
    def native_value(self):
        data = get_consumption(self.coordinator)
        value = number_value(data.get("consumption_value"))
        days = current_data_days(self.coordinator)

        if value is None or not days:
            return None

        return round(value / days, 2)

    @property
    def extra_state_attributes(self):
        data = get_consumption(self.coordinator)
        latest = get_latest_daily(self.coordinator)
        return {
            "dias_con_datos": current_data_days(self.coordinator),
            "hasta_fecha": latest.get("date"),
            "fecha_resumen_api": data.get("date_last_data"),
            "media_api": data.get("average_value"),
            "metodo": "consumo_periodo / días con curvas disponibles",
        }


class NibaCurrentAverageCostSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Coste medio diario de energía",
            "niba_average_cost",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        data = get_consumption(self.coordinator)
        value = number_value(data.get("consumption_amount"))
        days = current_data_days(self.coordinator)

        if value is None or not days:
            return None

        return round(value / days, 2)

    @property
    def extra_state_attributes(self):
        data = get_consumption(self.coordinator)
        latest = get_latest_daily(self.coordinator)
        return {
            "dias_con_datos": current_data_days(self.coordinator),
            "hasta_fecha": latest.get("date"),
            "fecha_resumen_api": data.get("date_last_data"),
            "media_api": amount_value(data.get("average_amount")),
            "metodo": "coste_energia_periodo / días con curvas disponibles",
        }


class NibaPeriodTotalDaysSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Días periodo",
            "niba_period_total_days",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_native_unit_of_measurement = "días"

    @property
    def native_value(self):
        return current_period_days(self.coordinator)


class NibaPeriodDataDaysSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Días con datos",
            "niba_period_data_days",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_native_unit_of_measurement = "días"

    @property
    def native_value(self):
        return current_data_days(self.coordinator)


class NibaPeriodProgressSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Progreso periodo",
            "niba_period_progress",
        )
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        total = current_period_days(self.coordinator)
        available = current_data_days(self.coordinator)

        if not total or available is None:
            return None

        return round(min(available / total * 100, 100), 1)


# ---------------------------------------------------------
# FACTURA ANTERIOR REAL Y COMPARATIVA
# ---------------------------------------------------------

class NibaPreviousPeriodConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo factura anterior",
            "niba_previous_period_consumption",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        # Preserve the state class previously exposed by this same unique_id.
        # Home Assistant already has long-term statistics for the entity.
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_bill(self.coordinator).get("act_total_consumption")


class NibaPreviousPeriodCostSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Total factura anterior",
            "niba_previous_period_cost",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return amount_value(get_latest_bill(self.coordinator).get("total_amount"))


class NibaProjectionVsPreviousSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Proyección vs factura anterior",
            "niba_projection_vs_previous",
        )
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    def _values(self):
        consumption = get_consumption(self.coordinator)
        bill = get_latest_bill(self.coordinator)

        projected = number_value(
            consumption.get("estimated_consumption_value")
        )
        previous = number_value(bill.get("act_total_consumption"))
        projected_days = current_period_days(self.coordinator)
        previous_days = bill_days(bill)

        if (
            projected is None
            or previous is None
            or not projected_days
            or not previous_days
            or previous == 0
        ):
            return None

        projected_daily = projected / projected_days
        previous_daily = previous / previous_days

        if previous_daily == 0:
            return None

        return {
            "projected": projected,
            "previous": previous,
            "projected_days": projected_days,
            "previous_days": previous_days,
            "projected_daily": projected_daily,
            "previous_daily": previous_daily,
            "percentage": (
                (projected_daily - previous_daily) / previous_daily * 100
            ),
        }

    @property
    def native_value(self):
        values = self._values()
        return round(values["percentage"], 1) if values else None

    @property
    def extra_state_attributes(self):
        values = self._values()
        bill = get_latest_bill(self.coordinator)
        consumption = get_consumption(self.coordinator)

        if not values:
            return {}

        return {
            "proyeccion_actual_kwh": round(values["projected"], 2),
            "dias_periodo_actual": values["projected_days"],
            "proyeccion_actual_kwh_dia": round(
                values["projected_daily"], 2
            ),
            "factura_anterior_kwh": round(values["previous"], 2),
            "dias_factura_anterior": values["previous_days"],
            "factura_anterior_kwh_dia": round(
                values["previous_daily"], 2
            ),
            "periodo_actual_inicio": consumption.get("start_at"),
            "factura_anterior_inicio": bill.get("start_at"),
            "factura_anterior_fin": bill.get("end_at"),
            "metodo": "comparación de medias diarias normalizadas",
        }


class NibaPreviousPeriodInfoSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Factura anterior periodo",
            "niba_previous_period_info",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        bill = get_latest_bill(self.coordinator)
        start = bill.get("start_at")
        end = bill.get("end_at")

        if start and end:
            return f"{start} → {end}"

        return "Sin factura"

    @property
    def extra_state_attributes(self):
        bill = get_latest_bill(self.coordinator)

        if not bill:
            return {}

        return {
            "inicio": bill.get("start_at"),
            "fin": bill.get("end_at"),
            "dias": bill_days(bill),
            "consumo_kwh": bill.get("act_total_consumption"),
            "total_eur": amount_value(bill.get("total_amount")),
            "energia_eur": amount_value(bill.get("nm_term_ener")),
            "potencia_eur": amount_value(bill.get("term_power")),
            "excedentes_kwh": bill.get("self_consumption_surplus"),
            "compensacion_eur": amount_value(
                bill.get("self_consumption_discount")
            ),
            "estado": bill_status_text(bill.get("status")),
            "codigo_factura": bill.get("billing_code"),
        }


# ---------------------------------------------------------
# SENSORES DE LA ÚLTIMA FACTURA
# ---------------------------------------------------------

class NibaLatestBillFieldSensor(NibaGenericSensor):
    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        super().__init__(
            coordinator,
            name,
            unique_id,
            get_latest_bill,
            path,
            **kwargs,
        )


class NibaLatestBillTotalSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última factura total",
            "niba_latest_bill_total",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return amount_value(get_latest_bill(self.coordinator).get("total_amount"))

    @property
    def extra_state_attributes(self):
        bill = get_latest_bill(self.coordinator)

        if not bill:
            return {}

        return {
            "codigo_factura": bill.get("billing_code"),
            "fecha_emision": bill.get("contab_date"),
            "inicio": bill.get("start_at"),
            "fin": bill.get("end_at"),
            "dias": bill_days(bill),
            "estado": bill_status_text(bill.get("status")),
            "consumo_kwh": bill.get("act_total_consumption"),
            "energia_eur": amount_value(bill.get("nm_term_ener")),
            "potencia_eur": amount_value(bill.get("term_power")),
            "excedentes_facturados_kwh": bill.get(
                "self_consumption_surplus"
            ),
            "excedentes_medidos_kwh": bill.get("total_surplus"),
            "compensacion_eur": amount_value(
                bill.get("self_consumption_discount")
            ),
            "alquiler_eur": amount_value(bill.get("nm_rental_amount")),
            "financiacion_bono_social_eur": bill_social_charge(bill),
            "impuesto_electrico_eur": amount_value(
                bill.get("nm_amount_ie")
            ),
            "iva_eur": amount_value(bill.get("tax_amount")),
            "impuestos_totales_eur": bill_total_taxes(bill),
            "base_iva_eur": amount_value(bill.get("base_amount")),
            "potencia_contratada_p1_kw": bill.get("contract_pwr_p1"),
            "potencia_contratada_p2_kw": bill.get("contract_pwr_p2"),
            "potencia_demandada": bill.get("demand_pwr"),
            "document_id": bill.get("document_id"),
        }


class NibaLatestBillPeriodSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última factura periodo",
            "niba_latest_bill_period",
        )

    @property
    def native_value(self):
        bill = get_latest_bill(self.coordinator)
        start = bill.get("start_at")
        end = bill.get("end_at")

        if start and end:
            return f"{start} → {end}"

        return None

    @property
    def extra_state_attributes(self):
        bill = get_latest_bill(self.coordinator)
        return {
            "inicio": bill.get("start_at"),
            "fin": bill.get("end_at"),
            "dias": bill_days(bill),
        }


class NibaLatestBillTaxesSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última factura impuestos",
            "niba_latest_bill_taxes",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return bill_total_taxes(get_latest_bill(self.coordinator))

    @property
    def extra_state_attributes(self):
        bill = get_latest_bill(self.coordinator)
        return {
            "impuesto_electrico_eur": amount_value(
                bill.get("nm_amount_ie")
            ),
            "iva_eur": amount_value(bill.get("tax_amount")),
        }


class NibaLatestBillSocialChargeSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última factura financiación bono social",
            "niba_latest_bill_social_charge",
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        return bill_social_charge(get_latest_bill(self.coordinator))

    @property
    def extra_state_attributes(self):
        return {
            "metodo": "calculado a partir de la base imponible de la factura"
        }


class NibaLatestBillStatusSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última factura estado",
            "niba_latest_bill_status",
        )

    @property
    def native_value(self):
        return bill_status_text(get_latest_bill(self.coordinator).get("status"))

    @property
    def extra_state_attributes(self):
        bill = get_latest_bill(self.coordinator)
        return {
            "estado_api": bill.get("status"),
            "codigo_factura": bill.get("billing_code"),
            "fecha_emision": bill.get("contab_date"),
        }


# ---------------------------------------------------------
# DAILY / GRID
# ---------------------------------------------------------

class NibaDailyConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo diario",
            "niba_consumo_diario",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_daily(self.coordinator).get("total_active_value")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get("consumption_daily", {})
        daily = (
            data.get("daily_consumption", [])
            if isinstance(data, dict)
            else []
        )

        if not isinstance(daily, list):
            daily = []

        daily = sorted(
            (day for day in daily if isinstance(day, dict)),
            key=lambda day: str(day.get("date") or ""),
        )

        return {
            "fecha": get_latest_daily(self.coordinator).get("date"),
            "dias_disponibles": len(daily),
            "historico": [
                {
                    "fecha": day.get("date"),
                    "importacion_kwh": day.get("total_active_value"),
                    "exportacion_kwh": day.get("total_out_active_value"),
                    "reactiva": day.get("total_reactive_value"),
                    "coste": amount_value(
                        day.get("daily_consumption_amount")
                    ),
                }
                for day in daily
            ],
        }


class NibaGridImportSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Importación red último día",
            "niba_grid_import",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_daily(self.coordinator).get("total_active_value")

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)

        return {
            "fecha": latest.get("date"),
            "medidas_horarias": [
                {
                    "hora": row.get("hour"),
                    "kwh": row.get("active_value"),
                }
                for row in latest.get("hour_measurements", [])
                if isinstance(row, dict)
            ],
        }


class NibaGridExportSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Exportación red último día",
            "niba_grid_export",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_daily(self.coordinator).get("total_out_active_value")

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)

        return {
            "fecha": latest.get("date"),
            "medidas_horarias": [
                {
                    "hora": row.get("hour"),
                    "kwh": row.get("out_active_value"),
                }
                for row in latest.get("hour_measurements", [])
                if isinstance(row, dict)
            ],
        }


class NibaDailyReactiveSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Energía reactiva diaria",
            "niba_daily_reactive",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_native_unit_of_measurement = "kVArh"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return get_latest_daily(self.coordinator).get("total_reactive_value")


class NibaDailyCostSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Coste diario API",
            "niba_daily_cost",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self):
        latest = get_latest_daily(self.coordinator)
        return amount_value(latest.get("daily_consumption_amount"))

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)
        return {
            "fecha": latest.get("date"),
            "fuente": "API Niba",
            "nota": "Valor proporcionado directamente por Niba",
        }


class NibaHourlyProfileSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Perfil horario",
            "niba_hourly_profile",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        measurements = get_latest_daily(self.coordinator).get(
            "hour_measurements", []
        )
        return len(measurements) if isinstance(measurements, list) else 0

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)
        return {
            "fecha": latest.get("date"),
            "medidas": latest.get("hour_measurements", []),
        }


class NibaLatestEnergyDateSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Fecha última curva",
            "niba_latest_energy_date",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        return get_latest_daily(self.coordinator).get("date")


class NibaLatestEnergyHourSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Última hora medida",
            "niba_latest_energy_hour",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        measurements = get_latest_daily(self.coordinator).get(
            "hour_measurements", []
        )

        if not isinstance(measurements, list):
            return None

        valid = [
            row
            for row in measurements
            if isinstance(row, dict) and row.get("hour") is not None
        ]

        if not valid:
            return None

        valid.sort(key=lambda row: str(row.get("hour") or ""))
        return valid[-1].get("hour")


class NibaLatestHourImportSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Importación última hora",
            "niba_latest_hour_import",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    def _latest_measurement(self):
        measurements = get_latest_daily(self.coordinator).get(
            "hour_measurements", []
        )

        if not isinstance(measurements, list):
            return {}

        valid = [
            row
            for row in measurements
            if isinstance(row, dict) and row.get("hour") is not None
        ]
        valid.sort(key=lambda row: str(row.get("hour") or ""))
        return valid[-1] if valid else {}

    @property
    def native_value(self):
        return self._latest_measurement().get("active_value")

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)
        row = self._latest_measurement()
        return {"fecha": latest.get("date"), "hora": row.get("hour")}


class NibaLatestHourExportSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Exportación última hora",
            "niba_latest_hour_export",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    def _latest_measurement(self):
        measurements = get_latest_daily(self.coordinator).get(
            "hour_measurements", []
        )

        if not isinstance(measurements, list):
            return {}

        valid = [
            row
            for row in measurements
            if isinstance(row, dict) and row.get("hour") is not None
        ]
        valid.sort(key=lambda row: str(row.get("hour") or ""))
        return valid[-1] if valid else {}

    @property
    def native_value(self):
        return self._latest_measurement().get("out_active_value")

    @property
    def extra_state_attributes(self):
        latest = get_latest_daily(self.coordinator)
        row = self._latest_measurement()
        return {"fecha": latest.get("date"), "hora": row.get("hour")}


# ---------------------------------------------------------
# MONTHLY
# ---------------------------------------------------------

class NibaMonthlyConsumptionValueSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo mensual",
            "niba_monthly_consumption_value",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_monthly(self.coordinator).get("consumption_value")

    @property
    def extra_state_attributes(self):
        data = get_latest_monthly(self.coordinator)
        return {
            "fecha": data.get("date"),
            "limite_a": data.get("a_limit"),
            "limite_b": data.get("b_limit"),
            "fuente": "endpoint mensual de Niba",
        }


class NibaMonthlyEstimatedConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Consumo mensual estimado",
            "niba_monthly_estimated_consumption",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_monthly(self.coordinator).get(
            "estimated_consumption_value"
        )


class NibaMonthlyAverageDailySensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Media diaria mensual API",
            "niba_monthly_daily_average",
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = None

    @property
    def native_value(self):
        value = get_latest_monthly(self.coordinator).get("consumption_day_avg")
        return round(float(value), 2) if isinstance(value, (int, float)) else value

    @property
    def extra_state_attributes(self):
        data = get_latest_monthly(self.coordinator)
        return {
            "fecha": data.get("date"),
            "fuente": "API Niba; no calculado por Home Assistant",
        }


class NibaMonthlySelfConsumptionSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Autoconsumo mensual",
            "niba_monthly_self_consumption",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return get_latest_monthly(self.coordinator).get(
            "self_consumption_energy"
        )


# ---------------------------------------------------------
# BALANCES
# ---------------------------------------------------------

class NibaBalanceBaseSensor(NibaBaseSensor):
    field = ""

    @property
    def native_value(self):
        data = self.coordinator.data.get("balances", {})
        if not isinstance(data, dict):
            return None
        return amount_value(data.get(self.field))


class NibaBalanceSensor(NibaBalanceBaseSensor):
    field = "amount"

    def __init__(self, coordinator):
        super().__init__(coordinator, "Niba Saldo disponible", "niba_balance")
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY


class NibaPendingBalanceSensor(NibaBalanceBaseSensor):
    field = "pending_amount"

    def __init__(self, coordinator):
        super().__init__(
            coordinator, "Niba Saldo pendiente", "niba_pending_balance"
        )
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY


class NibaTotalLoadedSensor(NibaBalanceBaseSensor):
    field = "total_loaded"

    def __init__(self, coordinator):
        super().__init__(coordinator, "Niba Total cargado", "niba_total_loaded")
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL


class NibaTotalSpentSensor(NibaBalanceBaseSensor):
    field = "total_spent"

    def __init__(self, coordinator):
        super().__init__(coordinator, "Niba Total gastado", "niba_total_spent")
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL


class NibaSolarBatterySensor(NibaBalanceBaseSensor):
    field = "solar_battery"

    def __init__(self, coordinator):
        super().__init__(coordinator, "Niba Solar Battery", "niba_solar_battery")
        self._attr_native_unit_of_measurement = CURRENCY_EURO
        self._attr_device_class = SensorDeviceClass.MONETARY


# ---------------------------------------------------------
# USER
# ---------------------------------------------------------

class NibaUserFieldSensor(NibaBaseSensor):
    def __init__(self, coordinator, name, unique_id, field):
        super().__init__(coordinator, name, unique_id)
        self._field = field

    @property
    def native_value(self):
        user = self.coordinator.data.get("user", {})

        if not isinstance(user, dict):
            return None

        value = user.get(self._field)
        return None if isinstance(value, (dict, list)) else value

    @property
    def extra_state_attributes(self):
        user = self.coordinator.data.get("user", {})

        if not isinstance(user, dict):
            return {}

        document = user.get("document") or {}
        phone = user.get("phone") or {}
        datadis = user.get("datadis") or {}

        return {
            "document_type": document.get("type"),
            "document_code": document.get("code"),
            "telefono": phone.get("number"),
            "prefijo_telefono": phone.get("prefix"),
            "nacionalidad": user.get("nationality"),
            "idioma": user.get("language"),
            "proveedor_autenticacion": user.get("auth_provider"),
            "account_id": user.get("account_id"),
            "contact_email": user.get("contact_email"),
            "is_guest": user.get("is_guest"),
            "is_channels_user": user.get("is_channels_user"),
            "datadis_estado": datadis.get("status"),
            "datadis_fecha": datadis.get("date"),
            "datadis_cups": datadis.get("cups"),
        }


class NibaUserEmailValidatedSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Email validado",
            "niba_usuario_email_validado",
        )

    @property
    def native_value(self):
        user = self.coordinator.data.get("user", {})
        if not isinstance(user, dict):
            return None
        return bool_text(user.get("is_validated"))


class NibaDatadisSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Datadis",
            "niba_datadis_status",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        user = self.coordinator.data.get("user", {})
        if not isinstance(user, dict):
            return None
        datadis = user.get("datadis") or {}
        return datadis.get("status")

    @property
    def extra_state_attributes(self):
        user = self.coordinator.data.get("user", {})
        datadis = user.get("datadis") or {} if isinstance(user, dict) else {}
        return {"fecha": datadis.get("date"), "cups": datadis.get("cups")}


# ---------------------------------------------------------
# CONTRACT
# ---------------------------------------------------------

class NibaContractFieldSensor(NibaGenericSensor):
    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        super().__init__(
            coordinator,
            name,
            unique_id,
            lambda c: c.data.get("contract", {}),
            path,
            **kwargs,
        )


class NibaContractProductSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Contrato producto",
            "niba_contrato_producto",
        )

    @property
    def native_value(self):
        contract = self.coordinator.data.get("contract", {})

        if not isinstance(contract, dict):
            return None

        product = contract.get("product")

        if not isinstance(product, dict):
            return product

        return (
            product.get("description")
            or product.get("name")
            or product.get("code")
        )

    @property
    def extra_state_attributes(self):
        contract = self.coordinator.data.get("contract", {})
        product = (
            contract.get("product") or {}
            if isinstance(contract, dict)
            else {}
        )

        if not isinstance(product, dict):
            return {}

        return {
            "code": product.get("code"),
            "name": product.get("name"),
            "family": product.get("family"),
            "family_code": product.get("family_code"),
            "atr_tariff": product.get("atr_tariff"),
            "min_power": product.get("min_power"),
            "max_power": product.get("max_power"),
            "powers": product.get("powers"),
            "self_consumption": product.get("self_consumption"),
        }


class NibaContractAddressSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Contrato dirección",
            "niba_contrato_direccion",
        )

    @property
    def native_value(self):
        contract = self.coordinator.data.get("contract", {})
        if not isinstance(contract, dict):
            return None
        return format_address(contract.get("address"))


class NibaContractV2Sensor(NibaGenericSensor):
    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        super().__init__(
            coordinator,
            name,
            unique_id,
            get_contract_v2,
            path,
            **kwargs,
        )


# ---------------------------------------------------------
# CONTRACTED POWER
# ---------------------------------------------------------

class NibaPowerContractedSensor(NibaBaseSensor):
    def __init__(self, coordinator, key, name, unique_id):
        super().__init__(coordinator, name, unique_id)
        self._key = key
        self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
        self._attr_device_class = SensorDeviceClass.POWER

    def _power(self) -> dict:
        contract = get_contract_v2(self.coordinator)
        powers = nested_value(contract, ("product", "powers"))

        if not isinstance(powers, list):
            return {}

        for power in powers:
            if isinstance(power, dict) and power.get("key") == self._key:
                return power

        return {}

    @property
    def native_value(self):
        return self._power().get("value")

    @property
    def extra_state_attributes(self):
        data = self._power()
        return {
            "periodo": data.get("key"),
            "descripcion": data.get("description"),
            "horario": data.get("schedule"),
        }


# ---------------------------------------------------------
# SELF CONSUMPTION
# ---------------------------------------------------------

class NibaSelfConsumptionSensor(NibaGenericSensor):
    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        def source(c):
            contract = get_contract_v2(c)
            value = nested_value(contract, ("product", "self_consumption"))
            return value if isinstance(value, dict) else {}

        super().__init__(
            coordinator,
            name,
            unique_id,
            source,
            path,
            **kwargs,
        )


# ---------------------------------------------------------
# CUPS TECHNICAL
# ---------------------------------------------------------

class NibaCupsTechnicalSensor(NibaGenericSensor):
    @staticmethod
    def watts_to_kw(value):
        if value in (None, ""):
            return None

        try:
            return round(float(value) / 1000, 3)
        except (TypeError, ValueError):
            return None

    def __init__(self, coordinator, name, unique_id, path, **kwargs):
        super().__init__(
            coordinator,
            name,
            unique_id,
            get_cups_object,
            path,
            **kwargs,
        )


# ---------------------------------------------------------
# COLLECTION / VARIABLE API DATA
# ---------------------------------------------------------

class NibaCollectionSensor(NibaBaseSensor):
    def __init__(
        self,
        coordinator,
        name,
        unique_id,
        key,
        *,
        entity_category=None,
    ):
        super().__init__(
            coordinator,
            name,
            unique_id,
            entity_category=entity_category,
        )
        self._key = key

    def _value(self):
        return self.coordinator.data.get(self._key)

    @property
    def native_value(self):
        value = self._value()

        if isinstance(value, (list, dict)):
            return len(value)
        if value is None:
            return 0
        return 1

    @property
    def extra_state_attributes(self):
        value = self._value()

        if isinstance(value, list):
            return {"items": value, "elementos": len(value)}
        if isinstance(value, dict):
            return {"data": value, "elementos": len(value)}
        return {"data": value}


class NibaMovementsSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "Niba Movimientos", "niba_movements")

    @property
    def native_value(self):
        data = self.coordinator.data.get("movements", {})
        if not isinstance(data, dict):
            return 0
        items = data.get("items", [])
        return len(items) if isinstance(items, list) else 0

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get("movements", {})
        if not isinstance(data, dict):
            return {}
        return {"items": data.get("items", []), "next": data.get("next")}


class NibaDocumentsSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Documentos",
            "niba_documents",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        documents = self.coordinator.data.get("documents", [])
        return len(documents) if isinstance(documents, list) else 0

    @property
    def extra_state_attributes(self):
        documents = self.coordinator.data.get("documents", [])

        if not isinstance(documents, list):
            return {}

        return {
            "documentos": [
                {
                    "document_id": item.get("document_id"),
                    "document_type": item.get("document_type"),
                    "disponible": bool(
                        (item.get("data") or {}).get("document")
                    ),
                }
                for item in documents
                if isinstance(item, dict)
            ]
        }


class NibaProductsCatalogSensor(NibaBaseSensor):
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "Niba Catálogo productos",
            "niba_products_catalog",
            entity_category=EntityCategory.DIAGNOSTIC,
        )

    @property
    def native_value(self):
        products = self.coordinator.data.get("products", [])
        return len(products) if isinstance(products, list) else 0

    @property
    def extra_state_attributes(self):
        products = self.coordinator.data.get("products", [])

        if not isinstance(products, list):
            return {}

        summary = []

        for product in products:
            if not isinstance(product, dict):
                continue

            summary.append(
                {
                    "id": product.get("id"),
                    "code": product.get("code"),
                    "name": product.get("name"),
                    "family": product.get("family"),
                    "client_type": product.get("client_type"),
                    "client_types": product.get("client_types"),
                    "self_consumption": product.get("self_consumption"),
                    "solar_prices": product.get("solar_prices"),
                }
            )

        return {"productos": summary}
