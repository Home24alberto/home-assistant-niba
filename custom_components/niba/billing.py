"""Date and consumption helpers for Niba period sensors."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from math import isfinite


def numeric(value):
    """Return a finite numeric value, rejecting booleans and malformed data."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def as_date(value):
    """Parse an ISO date or datetime value."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def period_matches(data, start, end):
    """Check whether a cached API summary belongs to the requested period."""
    return (
        isinstance(data, dict)
        and as_date(data.get("start_at")) == start
        and as_date(data.get("end_at")) == end
    )


def month_bounds(today, contract_start=None):
    """Return this calendar month, starting later if the contract is new."""
    start = today.replace(day=1)
    activation = as_date(contract_start)
    if activation and activation > start:
        start = min(activation, today)
    return start, today.replace(day=monthrange(today.year, today.month)[1])


def estimate(daily, start, end, today):
    """Summarize published curves and project kWh without inventing readings.

    The estimate is an arithmetic projection of closed days received from
    Niba. It deliberately does not calculate euros: each user's tariff and
    invoice charges are not guaranteed to be available from this endpoint.
    """
    period_days = (end - start).days + 1
    closed_through = min(end, today - timedelta(days=1))
    valid = {}

    for row in daily:
        if not isinstance(row, dict):
            continue
        day = as_date(row.get("date"))
        imported = numeric(row.get("total_active_value"))
        exported = numeric(row.get("total_out_active_value"))
        if (
            day is None
            or day < start
            or day > closed_through
            or imported is None
            or imported < 0
        ):
            continue
        # Keep the final version if Niba sends the same date more than once.
        valid[day] = (imported, exported if exported is not None and exported >= 0 else None)

    result = {
        "inicio": start.isoformat(),
        "fin": end.isoformat(),
        "dias_periodo": period_days,
        "dias_con_datos": len(valid),
        "dias_con_excedentes": sum(exported is not None for _, exported in valid.values()),
        "incluye_total_factura": False,
        "provisional": True,
        "fuente": "Curvas diarias publicadas por Niba",
        "metodo": "Media de días cerrados disponibles; proyección en kWh",
    }

    if not valid:
        result["motivo"] = "Niba aún no ha publicado curvas diarias cerradas para este periodo"
        return result

    imported_total = sum(imported for imported, _ in valid.values())
    exported_values = [exported for _, exported in valid.values() if exported is not None]
    result.update(
        {
            "consumo_medido_kwh": round(imported_total, 3),
            "consumo_estimado_kwh": round(
                imported_total / len(valid) * period_days, 3
            ),
            "hasta_fecha": max(valid).isoformat(),
        }
    )
    if exported_values:
        result.update(
            {
                "excedentes_medidos_kwh": round(sum(exported_values), 3),
                "excedentes_estimados_kwh": round(
                    sum(exported_values) / len(exported_values) * period_days,
                    3,
                ),
            }
        )
    return result
