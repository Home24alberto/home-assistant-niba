from copy import deepcopy
from datetime import date
import json
from niba_test.history import CycleHistory, supply_key
from niba_test.invoice import estimate_invoice, parse_text
from test_invoice import BILL, TEXT, contract, row


def test_zero_only_first_day_with_valid_tariff():
    tariff = parse_text(TEXT, BILL)
    start, end = date(2026,10,1), date(2026,10,31)
    first = estimate_invoice([], start, end, start, tariff, contract())
    assert first['acumulado']['total_eur'] == 0
    assert first['estado'] == 'inicio_ciclo_sin_dias_cerrados'
    assert 'prevision' not in first
    later = estimate_invoice([],start,end,date(2026,10,2),tariff,contract())
    assert 'acumulado' not in later and later['estado']=='sin_curvas'
    assert 'acumulado' not in estimate_invoice([],start,end,start,None,contract())


def test_restart_rollover_failure_and_supply_isolation():
    h=CycleHistory();key=supply_key('secret-cups')
    start,end=date(2026,10,1),date(2026,10,31)
    calc={'estado':'calculado_provisional','dias_valorados':30,'hasta_fecha':'2026-10-30','acumulado':{'total_eur':60}}
    daily=[row(start)]
    h.update(key,start,end,end,daily,calc)
    # Round-trip actual JSON represents a clean restart, not just same object.
    h=CycleHistory(json.loads(json.dumps(h.data)))
    assert h.restore_days(key,start)==daily
    h.update(key,start,end,end,daily,{'estado':'sin_tarifa_validada'})
    assert h.cycles(key)['2026-10-01']['calculation']==calc
    h.update(key,date(2026,11,1),date(2026,11,30),date(2026,11,1),[],{'estado':'sin_curvas'})
    summary=h.summary(key,[])
    assert summary['ciclos_guardados']==2 and summary['ciclos_cerrados']==1
    assert summary['ultimo_cierre']['total_calculado_eur']==60
    assert not summary['ultimo_cierre']['completo']
    before=deepcopy(h.cycles(key)['2026-10-01'])
    h.update(key,start,end,date(2026,11,2),[],{'acumulado':{'total_eur':0}})
    assert h.cycles(key)['2026-10-01']==before
    assert h.summary(supply_key('other'),[])['ciclos_guardados']==0
    assert 'secret-cups' not in json.dumps(h.data)


def test_comparison_requires_exact_dates_and_complete_calculation():
    h=CycleHistory();key='one'
    start,end=date(2026,10,1),date(2026,10,31)
    calc={'dias_valorados':31,'hasta_fecha':'2026-10-31','acumulado':{'total_eur':60}}
    h.update(key,start,end,end,[],calc)
    h.update(key,date(2026,11,1),date(2026,11,30),date(2026,11,1),[],{})
    wrong={'start_at':'2026-09-01','end_at':'2026-09-30','total_amount':75.03}
    assert 'diferencia_eur' not in h.summary(key,[wrong])['ultimo_cierre']
    good={'start_at':'2026-10-01','end_at':'2026-10-31','total_amount':61.5}
    assert h.summary(key,[good])['ultimo_cierre']['diferencia_eur']==1.5
    assert 'diferencia_eur' not in h.summary(key,[good,good])['ultimo_cierre']
    h.cycles(key)['2026-10-01']['complete']=False
    assert h.summary(key,[good])['ultimo_cierre']['estado_comparacion']=='calculo_incompleto'
