"""Date and consumption helpers for Niba period sensors."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from math import isfinite
import unicodedata


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


def contract_tariff(contract, products):
    """Return API prices for the exact product attached to a contract.

    Product catalog entries are matched by their stable ID/code. We never
    select the first catalog item or guess a tariff from its description.
    """
    contract_product = contract.get("product") if isinstance(contract, dict) else None
    if not isinstance(contract_product, dict):
        return {"estado": "producto_contrato_no_disponible"}

    code = contract_product.get("code") or contract_product.get("id")
    if not isinstance(code, str) or not code:
        return {"estado": "codigo_producto_no_disponible"}

    product = next(
        (
            item
            for item in products
            if isinstance(item, dict)
            and code.casefold()
            in {
                str(item.get("id") or "").casefold(),
                str(item.get("code") or "").casefold(),
            }
        ),
        None,
    ) if isinstance(products, list) else None

    if product is None:
        return {"estado": "producto_no_encontrado", "codigo": code}

    result = {
        "estado": "disponible",
        "codigo": code,
        "nombre": product.get("name"),
        "energia_punta_eur_kwh": None,
        "energia_llano_eur_kwh": None,
        "energia_valle_eur_kwh": None,
        "potencia_punta_eur_kw_dia": None,
        "potencia_valle_eur_kw_dia": None,
        "excedentes_eur_kwh": None,
    }

    field_map = {
        ("energia", "punta"): "energia_punta_eur_kwh",
        ("energia", "llano"): "energia_llano_eur_kwh",
        ("energia", "valle"): "energia_valle_eur_kwh",
        ("potencia", "punta"): "potencia_punta_eur_kw_dia",
        ("potencia", "valle"): "potencia_valle_eur_kw_dia",
    }

    def normalized(value):
        value = unicodedata.normalize("NFKD", str(value or "").casefold())
        return "".join(char for char in value if not unicodedata.combining(char))

    prices = product.get("prices")
    if isinstance(prices, list):
        for item in prices:
            if not isinstance(item, dict):
                continue
            group = normalized(item.get("group"))
            label = normalized(item.get("label"))
            key = field_map.get((group, label))
            amount = numeric(item.get("value"))
            if key and amount is not None:
                result[key] = amount

    solar = product.get("solar_prices")
    fixed = solar.get("fixed_price") if isinstance(solar, dict) else None
    if isinstance(fixed, dict):
        result["excedentes_eur_kwh"] = numeric(fixed.get("value"))

    return result


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
