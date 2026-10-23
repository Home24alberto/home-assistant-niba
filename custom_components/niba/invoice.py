"""Validated invoice prices and hourly estimates (fixed 2.0TD, mainland/Balearics).

Only sanitized numeric results leave this module. A previous invoice is a price
reference, not proof that its rates still apply to the current billing cycle.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import re
import unicodedata
from zoneinfo import ZoneInfo

from .billing import as_date, numeric

D = Decimal
BANDS = ("punta", "llano", "valle")
HOLIDAYS = {(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1),
            (12, 6), (12, 8), (12, 25)}
NUM = r"([0-9]+(?:\.[0-9]{3})*(?:,[0-9]+)?)"


class InvoiceError(ValueError):
    """Controlled diagnostic code, never PDF text or identifiers."""


def money(value):
    return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def decimal(value):
    parsed = numeric(value)
    if parsed is None or parsed < 0:
        raise InvoiceError("valor_no_valido")
    return D(str(parsed))


def normalize(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def parse_text(text: str, bill: dict) -> dict:
    """Accept exactly one of each supported line and reconcile its amounts."""
    text = normalize(text)

    def line(pattern):
        matches = re.findall(pattern, text)
        if len(matches) != 1:
            raise InvoiceError("formato_factura_no_compatible")
        return tuple(D(v.replace(".", "").replace(",", ".")) for v in matches[0])

    start, end = as_date(bill.get("start_at")), as_date(bill.get("end_at"))
    if not start or not end or end < start:
        raise InvoiceError("periodo_factura_no_valido")
    days = D((end - start).days + 1)
    tariff = {"inicio_factura": start.isoformat(), "fin_factura": end.isoformat()}
    consumption, energy, power = {}, D(0), D(0)
    for band in BANDS:
        kwh, price, amount = line(r"energia activa " + band + r"\s+" + NUM + r"\s*kwh\s+" + NUM + r"\s*€/kwh\s+" + NUM + r"\s*€")
        if abs(money(kwh * price) - amount) > D("0.01"):
            raise InvoiceError("importe_energia_no_cuadra")
        tariff["energia_" + band] = str(price)
        consumption[band] = kwh
        energy += amount
    for band in ("punta", "valle"):
        kw, n, price, amount = line(r"importe potencia " + band + r"\s+" + NUM + r"\s*kw\s*-\s*" + NUM + r"\s*dias\s+" + NUM + r"\s*€/kw\s*dia\s+" + NUM + r"\s*€")
        if n != days or abs(money(kw * n * price) - amount) > D("0.01"):
            raise InvoiceError("importe_potencia_no_cuadra")
        tariff["potencia_" + band] = str(price)
        tariff["kw_" + band] = str(kw)
        power += amount
    for label, key in (("alquiler aparato medida", "alquiler"), ("financiacion bono social", "bono_social")):
        n, price, amount = line(label + r"\s+" + NUM + r"\s*dias\s+" + NUM + r"\s*€/dia\s+" + NUM + r"\s*€")
        if n != days or abs(money(n * price) - amount) > D("0.01"):
            raise InvoiceError("importe_fijo_no_cuadra")
        tariff[key] = str(price)
    tariff["excedentes"] = None
    exports, credit = D(0), D(0)
    if "compensacion excedentes en factura" in text:
        exports, price, credit = line(r"compensacion excedentes en factura\s+" + NUM + r"\s*kwh\s+" + NUM + r"\s*€/kwh\s*-" + NUM + r"\s*€")
        tariff["excedentes"] = str(price)
        if abs(min(energy, money(exports * price)) - credit) > D("0.01"):
            raise InvoiceError("compensacion_no_cuadra")
    ie_base, ie_rate, ie_amount = line(r"impuesto electrico\s+" + NUM + r"\s*€\s+" + NUM + r"\s*%\s+" + NUM + r"\s*€")
    vat_base, vat_rate, vat_amount = line(r"\biva\s+" + NUM + r"\s*€\s+" + NUM + r"\s*%\s+" + NUM + r"\s*€")
    tariff["impuesto_electrico"] = str(ie_rate / 100)
    tariff["iva"] = str(vat_rate / 100)
    # Reject credits/discounts/extra services not represented by this model.
    for key in ("wallet_surplus", "redeemed_balance", "nm_term_ener_desc"):
        value = numeric(bill.get(key))
        if value is not None and abs(value) > .005:
            raise InvoiceError("factura_con_ajustes_no_compatibles")
    check = calculate(consumption, exports, int(days), tariff)
    expected = {
        "act_total_consumption": sum(consumption.values()),
        "nm_term_ener": energy, "term_power": power,
        "self_consumption_discount": -credit,
        "nm_rental_amount": D(str(check["alquiler_eur"])),
        "nm_amount_ie": ie_amount, "tax_amount": vat_amount,
        "base_amount": vat_base, "total_amount": D(str(check["total_eur"])),
    }
    for key, value in expected.items():
        actual = numeric(bill.get(key))
        if actual is None or abs(D(str(actual)) - value) > D("0.02"):
            raise InvoiceError("factura_api_pdf_no_coinciden")
    if (abs(D(str(check["base_ie_eur"])) - ie_base) > D("0.02")
            or abs(D(str(check["base_iva_eur"])) - vat_base) > D("0.02")
            or abs(D(str(check["impuesto_electrico_eur"])) - ie_amount) > D("0.02")
            or abs(D(str(check["iva_eur"])) - vat_amount) > D("0.02")):
        raise InvoiceError("modelo_impuestos_no_compatible")
    tariff["validacion_total_eur"] = check["total_eur"]
    return tariff


def parse_pdf(content: bytes, bill: dict) -> dict:
    """Called in HA's executor; never save or log the document."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted or len(reader.pages) > 20:
            raise InvoiceError("pdf_no_compatible")
        return parse_text("\n".join(p.extract_text() or "" for p in reader.pages), bill)
    except InvoiceError:
        raise
    except Exception:
        raise InvoiceError("pdf_no_legible") from None


def calculate(kwh, exports, days, tariff):
    energy = sum((money(decimal(kwh[b]) * D(tariff["energia_" + b])) for b in BANDS), D(0))
    power = sum((money(D(tariff["kw_" + b]) * D(tariff["potencia_" + b]) * days) for b in ("punta", "valle")), D(0))
    credit = min(energy, money(decimal(exports) * D(tariff["excedentes"] or "0")))
    rental = money(D(tariff["alquiler"]) * days)
    social = money(D(tariff["bono_social"]) * days)
    base_ie = energy + power - credit + social
    ie = money(base_ie * D(tariff["impuesto_electrico"]))
    base_vat = base_ie + ie + rental
    vat = money(base_vat * D(tariff["iva"]))
    return {key: float(value) for key, value in {
        "energia_eur": energy, "potencia_eur": power, "compensacion_eur": credit,
        "alquiler_eur": rental, "bono_social_eur": social,
        "base_ie_eur": base_ie, "impuesto_electrico_eur": ie,
        "base_iva_eur": base_vat, "iva_eur": vat, "total_eur": base_vat + vat,
    }.items()}


def is_valley_day(day):
    return day.weekday() >= 5 or (day.month, day.day) in HOLIDAYS


def band_at(day, hour):
    if is_valley_day(day) or hour < 8:
        return "valle"
    return "punta" if 10 <= hour < 14 or 18 <= hour < 22 else "llano"


def expected_hours(day):
    zone = ZoneInfo("Europe/Madrid")
    stamp = datetime.combine(day, time(), zone).astimezone(timezone.utc)
    stop = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(timezone.utc)
    hours = []
    while stamp < stop:
        hours.append(stamp.astimezone(zone).hour)
        stamp += timedelta(hours=1)
    return Counter(hours)


def day_totals(row, day, solar):
    values = {b: D(0) for b in BANDS}
    exports = D(0)
    hours = []
    measurements = row.get("hour_measurements")
    if not isinstance(measurements, list):
        raise InvoiceError("curvas_horarias_incompletas")
    for item in measurements:
        if not isinstance(item, dict):
            raise InvoiceError("curvas_horarias_incompletas")
        match = re.fullmatch(r"([0-9]{2}):00(?::00)?", str(item.get("hour")))
        if not match or not 0 <= int(match[1]) <= 23:
            raise InvoiceError("formato_hora_no_compatible")
        hour = int(match[1])
        hours.append(hour)
        values[band_at(day, hour)] += decimal(item.get("active_value"))
        if solar:
            exports += decimal(item.get("out_active_value"))
    if Counter(hours) != expected_hours(day):
        raise InvoiceError("curvas_horarias_incompletas")
    if abs(sum(values.values()) - decimal(row.get("total_active_value"))) > D("0.025"):
        raise InvoiceError("curvas_diarias_horarias_no_coinciden")
    if solar and abs(exports - decimal(row.get("total_out_active_value"))) > D("0.025"):
        raise InvoiceError("excedentes_diarios_horarios_no_coinciden")
    return values, exports


def estimate_invoice(daily, start, end, today, tariff, contract):
    result = {"estado": "sin_tarifa_validada", "provisional": True,
              "fuente": "Curvas horarias y precios del contrato contrastados con la factura",
              "inicio": start.isoformat(), "fin": end.isoformat(),
              "supuesto": "Se mantienen precios, impuestos y condiciones de la factura de referencia"}
    if not tariff:
        return result
    result["tarifa"] = tariff
    reference = as_date(tariff.get("fin_factura"))
    activation = as_date(contract.get("start_at"))
    expiration = as_date(contract.get("end_at"))
    if (not reference or (today - reference).days > 90 or reference > today
            or (activation and activation > reference) or (expiration and expiration < today)):
        result["estado"] = "tarifa_requiere_nueva_factura"
        return result
    if contract.get("status") and str(contract["status"]).casefold() != "active":
        result["estado"] = "contrato_no_activo"
        return result
    product = contract.get("product") or {}
    if str(product.get("atr_tariff")) not in ("018", "2.0TD") :
        result["estado"] = "producto_no_compatible"
        return result
    for band, key in (("punta", "P1"), ("valle", "P2")):
        power = next((p.get("value") for p in product.get("powers", []) if p.get("key") == key), None)
        if numeric(power) is None or abs(decimal(power) - D(tariff["kw_" + band])) > D("0.001"):
            result["estado"] = "potencia_cambiada_desde_factura"
            return result
    solar_data = product.get("self_consumption") or {}
    solar = bool(solar_data.get("start_at"))
    if solar and (tariff["excedentes"] is None or solar_data.get("payment_type") != "FIXED"
                  or numeric(solar_data.get("usage_cl_price")) != numeric(tariff["excedentes"])):
        result["estado"] = "compensacion_no_validada"
        return result
    rows = {as_date(r.get("date")): r for r in daily if isinstance(r, dict)}
    closed = min(end, today - timedelta(days=1))
    totals = {b: D(0) for b in BANDS}
    exports = D(0)
    valid = {}
    day = start
    stopped = "sin_curvas"
    # Only a continuous prefix is accrued: missing days are never zero-filled.
    while day <= closed:
        if day not in rows:
            break
        try:
            values, out = day_totals(rows[day], day, solar)
        except InvoiceError as err:
            stopped = str(err)
            break
        valid[day] = (values, out)
        for b in BANDS:
            totals[b] += values[b]
        exports += out
        day += timedelta(days=1)
    if not valid:
        result["estado"] = stopped
        return result
    result.update({"estado": "calculado_provisional", "hasta_fecha": max(valid).isoformat(),
                   "dias_valorados": len(valid), "dias_pendientes_curvas": max(0, (closed - day).days + 1),
                   "consumo_kwh": float(sum(totals.values())), "excedentes_kwh": float(exports),
                   "consumo_por_periodo_kwh": {b: float(totals[b]) for b in BANDS},
                   "acumulado": calculate(totals, exports, len(valid), tariff),
                   "estado_prevision": "muestra_insuficiente"})
    result["acumulado"]["coste_medio_diario_eur"] = round(result["acumulado"]["energia_eur"] / len(valid), 2)
    result["ultimo_dia"] = calculate(*valid[max(valid)], 1, tariff)
    if (expiration and expiration < end) or (end - reference).days > 90:
        result["estado_prevision"] = "vigencia_no_cubre_periodo"
        return result
    # Two observations of each weekday; holiday days have their own valley group.
    def group(d):
        return 7 if is_valley_day(d) else d.weekday()
    period_days = (end - start).days + 1
    if len(valid) < min(14, period_days):
        return result
    groups = {}
    for d, value in valid.items():
        groups.setdefault(group(d), []).append(value)
    projected, projected_exports = dict(totals), exports
    while day <= end:
        samples = groups.get(group(day), [])
        if len(samples) < 2:
            return result
        for b in BANDS:
            projected[b] += sum((v[b] for v, _ in samples), D(0)) / len(samples)
        projected_exports += sum((out for _, out in samples), D(0)) / len(samples)
        day += timedelta(days=1)
    result.update({"estado_prevision": "calculada_provisional",
                   "metodo_prevision": "Media por día de la semana; fines de semana y festivos agrupados",
                   "consumo_previsto_kwh": round(float(sum(projected.values())), 3),
                   "prevision": calculate(projected, projected_exports, period_days, tariff)})
    return result
