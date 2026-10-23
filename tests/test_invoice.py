from datetime import date, timedelta
from decimal import Decimal as D
import pytest
from niba_test.invoice import (parse_text, InvoiceError, calculate, day_totals,
                              expected_hours, band_at, estimate_invoice)

# Synthetic invoice fixture, not a customer's document.
TEXT = '''
Importe potencia Punta 4,00 kW - 30 días 0,080 €/kW día 9,60€
Importe potencia Valle 4,00 kW - 30 días 0,060 €/kW día 7,20€
Energía Activa Punta 10 kWh 0,200 €/kWh 2,00€
Energía Activa Llano 20 kWh 0,150 €/kWh 3,00€
Energía Activa Valle 300 kWh 0,090 €/kWh 27,00€
Compensación excedentes en factura 100 kWh 0,060 €/kWh -6,00€
Alquiler Aparato Medida 30 días 0,030 €/día 0,90€
Financiación Bono Social 30 días 0,020 €/día 0,60€
Impuesto Eléctrico 43,40 € 5 % 2,17€
IVA 46,47 € 21 % 9,76€
Total a pagar 56,23€
'''
BILL = dict(start_at='2026-09-01', end_at='2026-09-30',
            act_total_consumption=330, nm_term_ener=[32, 'EUR'], term_power=[16.8, 'EUR'],
            self_consumption_discount=[-6,'EUR'], nm_rental_amount=[.9,'EUR'],
            nm_amount_ie=[2.17,'EUR'], tax_amount=[9.76,'EUR'], base_amount=[46.47,'EUR'],
            total_amount=[56.23,'EUR'])

@pytest.fixture
def tariff():
    return parse_text(TEXT, BILL)


def row(day, imported=1, exported=.1):
    hours = list(expected_hours(day).elements())
    return {'date': day.isoformat(), 'total_active_value': imported*len(hours),
            'total_out_active_value': exported*len(hours), 'hour_measurements': [
        {'hour': f'{h:02}:00:00', 'active_value': imported, 'out_active_value': exported}
        for h in hours]}


def contract():
    return {'start_at': '2026-08-21', 'end_at': '2027-08-21', 'product': {
        'code': 'FL_DISC_HORARIA', 'atr_tariff': '018',
        'powers': [{'key':'P1','value':4}, {'key':'P2','value':4}],
        'self_consumption': {'start_at':'2026-08-21','payment_type':'FIXED','usage_cl_price':.06}}}


def test_reference_reconciles(tariff):
    assert tariff['validacion_total_eur'] == 56.23
    assert tariff['energia_valle'] == '.090' or D(tariff['energia_valle']) == D('.09')

@pytest.mark.parametrize('change', [
    lambda t: t.replace('0,200', '0,300'),
    lambda t: t + '\nEnergía Activa Punta 10 kWh 0,200 €/kWh 2,00€',
    lambda t: t.replace('Financiación Bono Social', 'Otro concepto'),
    lambda t: t.replace('5 %', '6 %'),
])
def test_ambiguous_or_unreconciled_rejected(change):
    with pytest.raises(InvoiceError):
        parse_text(change(TEXT), BILL)


def test_other_adjustments_rejected():
    with pytest.raises(InvoiceError, match='ajustes'):
        parse_text(TEXT, dict(BILL, redeemed_balance=[5,'EUR']))


def test_cap_is_per_period(tariff):
    values = calculate({'punta':1,'llano':0,'valle':0}, 1000, 30, tariff)
    assert values['compensacion_eur'] == values['energia_eur'] == .2
    assert values['total_eur'] > 0  # Fixed costs cannot be cancelled by simple compensation.

@pytest.mark.parametrize('day,count', [(date(2026,3,29),23),(date(2026,10,25),25),(date(2026,10,19),24)])
def test_dst(day,count):
    assert sum(expected_hours(day).values()) == count
    v, _ = day_totals(row(day), day, True)
    assert sum(v.values()) == count


def test_missing_and_duplicate_hours_rejected():
    d = date(2026,10,19)
    r = row(d)
    r['hour_measurements'][-1] = r['hour_measurements'][0]
    with pytest.raises(InvoiceError):
        day_totals(r,d,True)


def test_daily_mismatch_rejected():
    d = date(2026,10,19)
    r = row(d); r['total_active_value'] = 99
    with pytest.raises(InvoiceError):
        day_totals(r,d,True)


def test_schedule():
    d = date(2026,10,19)
    assert [band_at(d,h) for h in (0,7,8,10,14,18,22,23)] == ['valle','valle','llano','punta','llano','punta','llano','llano']
    assert all(band_at(date(2026,10,12),h) == 'valle' for h in range(24))
    assert all(band_at(date(2026,10,18),h) == 'valle' for h in range(24))


def test_accumulation_and_forecast(tariff):
    start,end,today = date(2026,10,1),date(2026,10,31),date(2026,10,23)
    rows = [row(start+timedelta(days=i)) for i in range(19)]
    result = estimate_invoice(rows,start,end,today,tariff,contract())
    assert result['dias_valorados'] == 19
    assert result['dias_pendientes_curvas'] == 3
    assert result['estado_prevision'] == 'calculada_provisional'
    assert result['consumo_kwh'] == 456
    assert result['prevision']['total_eur'] > result['acumulado']['total_eur']


def test_gap_stops_accumulation(tariff):
    start = date(2026,10,1)
    rows = [row(start),row(start+timedelta(days=2))]
    result = estimate_invoice(rows,start,date(2026,10,31),date(2026,10,4),tariff,contract())
    assert result['dias_valorados'] == 1
    assert result['hasta_fecha'] == '2026-10-01'
    assert 'prevision' not in result


def test_no_prices_no_money():
    assert 'acumulado' not in estimate_invoice([],date(2026,10,1),date(2026,10,31),date(2026,10,23),None,contract())


def test_changed_power_and_stale_reference(tariff):
    c = contract();c['product']['powers'][0]['value'] = 6
    r = estimate_invoice([],date(2026,10,1),date(2026,10,31),date(2026,10,23),tariff,c)
    assert r['estado'] == 'potencia_cambiada_desde_factura'
    r = estimate_invoice([],date(2027,2,1),date(2027,2,28),date(2027,2,23),tariff,contract())
    assert r['estado'] == 'tarifa_requiere_nueva_factura'


def test_other_product_code_with_verified_prices(tariff):
    current = contract()
    current["product"]["code"] = "OTHER_VALIDATED_PRODUCT"
    day = date(2026, 10, 1)
    result = estimate_invoice([row(day)], day, date(2026, 10, 31),
                              date(2026, 10, 3), tariff, current)
    assert result["estado"] != "producto_no_compatible"
    assert result["acumulado"]["total_eur"] > 0

def test_flat_three_equal_prices(tariff):
    current = contract()
    current["product"]["code"] = "FIXED_PRICE_PRODUCT"
    fixed = dict(tariff, energia_punta="0.12", energia_llano="0.12", energia_valle="0.12")
    result = estimate_invoice([row(date(2026, 10, 1))], date(2026, 10, 1),
                              date(2026, 10, 31), date(2026, 10, 3), fixed, current)
    assert result["estado"] != "producto_no_compatible"
    assert result["acumulado"]["energia_eur"] > 0
