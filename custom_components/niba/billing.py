"""Generic billing helpers for the optional Niba invoice forecast."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite


def numeric(value):
    """Return a finite float from API values such as ``[value, currency]``."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def money(value):
    """Round monetary values with commercial half-up rounding."""
    return float(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def as_date(value):
    """Parse an ISO date returned by the API."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def month_bounds(today, contract_start=None):
    """Return the inclusive calendar-month billing window."""
    start = today.replace(day=1)
    activation = as_date(contract_start)
    if activation and activation > start:
        start = min(activation, today)
    return start, today.replace(day=monthrange(today.year, today.month)[1])


def valid_daily_rows(daily, start, end, today):
    """Return one coherent import/export curve per closed day."""
    valid = {}
    limit = min(end, today - timedelta(days=1))
    for row in daily:
        if not isinstance(row, dict):
            continue
        day = as_date(row.get("date"))
        imported = numeric(row.get("total_active_value"))
        exported = numeric(row.get("total_out_active_value"))
        if (
            day
            and start <= day <= limit
            and imported is not None
            and exported is not None
            and imported >= 0
            and exported >= 0
        ):
            valid[day] = row
    return valid


def consumption_summary(daily, start, end, today):
    """Summarize and project daily curves without needing tariff data."""
    days = (end - start).days + 1
    valid = valid_daily_rows(daily, start, end, today)
    result = {
        "inicio": start.isoformat(),
        "fin": end.isoformat(),
        "dias_periodo": days,
        "dias_con_datos": len(valid),
    }
    if not valid:
        return result

    imported = sum(numeric(row["total_active_value"]) for row in valid.values())
    exported = sum(numeric(row["total_out_active_value"]) for row in valid.values())
    scale = days / len(valid)
    result.update(
        {
            "consumo_medido_kwh": round(imported, 3),
            "excedentes_medidos_kwh": round(exported, 3),
            "consumo_estimado_kwh": round(imported * scale, 3),
            "excedentes_estimados_kwh": round(exported * scale, 3),
            "hasta_fecha": max(valid).isoformat(),
        }
    )
    return result


def tariff_period(day, hour, settings):
    """Return the Spanish 2.0TD period for an hour."""
    if day.weekday() >= 5 or day.isoformat() in settings["valley_dates"]:
        return "valle"
    if hour < 8:
        return "valle"
    if 10 <= hour < 14 or 18 <= hour < 22:
        return "punta"
    return "llano"


def invoice_parts(energy, exports, days, settings):
    """Build the complete forecast from user-configured contract values."""
    power = money(
        settings["power_p1_kw"] * settings["power_p1_day"] * days
    ) + money(settings["power_p2_kw"] * settings["power_p2_day"] * days)
    discount = -min(money(exports * settings["surplus_kwh"]), energy)
    social = money(days * settings["social_day"])
    rental = money(days * settings["rental_day"])
    electricity_base = money(energy + discount + power + social)
    electricity_tax = money(
        max(0, electricity_base) * settings["electricity_tax"]
    )
    vat_base = money(electricity_base + electricity_tax + rental)
    vat = money(vat_base * settings["vat"])
    return {
        "energia_eur": money(energy),
        "potencia_eur": money(power),
        "compensacion_eur": money(discount),
        "bono_social_eur": social,
        "alquiler_eur": rental,
        "impuesto_electrico_eur": electricity_tax,
        "iva_eur": vat,
        "total_eur": money(vat_base + vat),
    }


def estimate(daily, start, end, today, settings, bill=None):
    """Project a complete invoice from curves and per-user tariff options."""
    result = consumption_summary(daily, start, end, today)
    result.update(
        {
            "configurado": True,
            "incluye_total_factura": True,
            "provisional": True,
            "fuente_precios": "Opciones de la integración",
            "advertencia": (
                "Previsión según curvas y precios configurados; no incluye wallet. "
                "Revisa tarifas, impuestos y festivos cuando cambie el contrato."
            ),
        }
    )
    valid = valid_daily_rows(daily, start, end, today)
    if not valid:
        result["motivo"] = "Sin días cerrados con importación y exportación"
        return result

    imported_total = exported_total = 0.0
    buckets = dict.fromkeys(("punta", "llano", "valle"), 0.0)
    fallback_import = 0.0
    fallback_days = 0

    for day, row in sorted(valid.items()):
        imported = numeric(row["total_active_value"])
        exported = numeric(row["total_out_active_value"])
        imported_total += imported
        exported_total += exported
        readings = []
        hours = row.get("hour_measurements")
        if isinstance(hours, list):
            for reading in hours:
                if not isinstance(reading, dict):
                    readings = []
                    break
                try:
                    hour = int(str(reading.get("hour")).split(":")[0])
                except (TypeError, ValueError):
                    readings = []
                    break
                value = numeric(reading.get("active_value"))
                if hour not in range(24) or value is None or value < 0:
                    readings = []
                    break
                readings.append((hour, value))

        spring = day.month == 3 and day.weekday() == 6 and day.day + 7 > 31
        autumn = day.month == 10 and day.weekday() == 6 and day.day + 7 > 31
        expected = 23 if spring else 25 if autumn else 24
        unique_expected = 23 if spring else 24
        hours_ok = (
            len(readings) == expected
            and len({hour for hour, _ in readings}) == unique_expected
        )
        if hours_ok and abs(sum(value for _, value in readings) - imported) <= max(
            0.03, imported * 0.005
        ):
            for hour, value in readings:
                buckets[tariff_period(day, hour, settings)] += value
        else:
            fallback_days += 1
            fallback_import += imported

    scale = result["dias_periodo"] / len(valid)
    energy = sum(
        money(buckets[period] * scale * settings["energy_kwh"][period])
        for period in buckets
    )
    if fallback_days:
        bill = bill or {}
        previous_kwh = numeric(bill.get("act_total_consumption"))
        previous_energy = numeric(bill.get("nm_term_ener"))
        if not previous_kwh or previous_energy is None or previous_energy < 0:
            result["motivo"] = (
                "Curvas incompletas y sin factura de referencia para precio medio"
            )
            return result
        mean_price = previous_energy / previous_kwh
        energy += money(fallback_import * scale * mean_price)
        result["precio_medio_respaldo_eur_kwh"] = round(mean_price, 6)
        result["factura_referencia"] = bill.get("billing_code")

    result.update(
        invoice_parts(
            money(energy),
            exported_total * scale,
            result["dias_periodo"],
            settings,
        )
    )
    result.update(
        {
            "dias_precio_medio": fallback_days,
            "metodo": (
                "Media diaria de curvas; tarifa horaria"
                if not fallback_days
                else "Media diaria; curvas horarias y precio medio de última factura"
            ),
            "coste_energia_medido_eur": money(energy / scale),
        }
    )
    return result
