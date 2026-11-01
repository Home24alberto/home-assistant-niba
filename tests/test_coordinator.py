"""Coordinator orchestration with HA boundary stubs, not a live HA test."""
import asyncio
from datetime import datetime, timezone
import importlib
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock

from test_invoice import BILL, TEXT, contract, row
from datetime import date


def test_auto_tariff_download_cache_and_failure(monkeypatch):
    def module(name, **attrs):
        m=ModuleType(name)
        for key,value in attrs.items(): setattr(m,key,value)
        monkeypatch.setitem(sys.modules,name,m)
        return m
    class Coordinator:
        def __init__(self,hass,**kwargs): self.hass=hass
    class Store:
        saved = None
        def __init__(self, *args): pass
        async def async_load(self):
            from copy import deepcopy
            return deepcopy(Store.saved)
        async def async_save(self, data):
            from copy import deepcopy
            Store.saved = deepcopy(data)
    module('homeassistant.helpers.storage', Store=Store)
    class AuthError(Exception): pass
    class UpdateError(Exception): pass
    module('homeassistant')
    module('homeassistant.config_entries',ConfigEntry=object)
    module('homeassistant.core',HomeAssistant=object)
    module('homeassistant.exceptions',ConfigEntryAuthFailed=AuthError)
    module('homeassistant.helpers')
    module('homeassistant.helpers.aiohttp_client',async_get_clientsession=lambda hass:None)
    module('homeassistant.helpers.update_coordinator',DataUpdateCoordinator=Coordinator,UpdateFailed=UpdateError)
    module('homeassistant.util',dt=SimpleNamespace(now=lambda:datetime(2026,10,23,tzinfo=timezone.utc)))
    sys.modules.pop('niba_test.coordinator',None)
    mod=importlib.import_module('niba_test.coordinator')
    from niba_test.invoice import parse_text
    monkeypatch.setattr(mod,'parse_pdf',lambda pdf,bill:parse_text(TEXT,bill))
    prices={k:v for k,v in parse_text(TEXT,BILL).items() if k.startswith(('energia_', 'potencia_'))}
    monkeypatch.setattr(mod,'read_contract_pdf',lambda pdf:dict(prices))
    async def execute(fn,*args): return fn(*args)
    hass=SimpleNamespace(async_add_executor_job=execute)
    co=mod.NibaCoordinator(hass,SimpleNamespace(entry_id='test',data={'email':'test','password':'test'}))
    c=contract();c.update(cups='private-cups',status='Active',address={'postal':'28001'},document_id='private-contract')
    b=dict(BILL,document_id='private-doc',status='PAID')
    methods={
        'get_contracts':[c], 'get_contracts_v2':[c], 'get_cups_info':{},
        'get_consumption_period':{}, 'get_consumption_daily':[row(date(2026,10,1))],
        'get_consumption_monthly':[], 'get_balances':{}, 'get_bills':[b],
        'get_invoice_pdf':b'%PDF-mocked',
    }
    co.api=SimpleNamespace(**{k:AsyncMock(return_value=v) for k,v in methods.items()})
    async def run():
        first=await co._async_update_data()
        assert first['calculated_invoice']['estado']=='calculado_provisional'
        assert first['calculated_invoice']['tarifa']['validacion_total_eur']==56.23
        assert 'private' not in repr(first)
        await co._async_update_data()
        assert co.api.get_invoice_pdf.await_count==2
        co._contract_prices['energia_valle']='9.99'
        mismatch=await co._async_update_data()
        assert mismatch['calculated_invoice']['estado']=='precios_contrato_factura_distintos'
        assert 'acumulado' not in mismatch['calculated_invoice']
        co._contract_prices=dict(prices)
        co.api.get_bills.return_value=[dict(b,document_id='new-doc')]
        co.api.get_invoice_pdf.side_effect=mod.NibaApiError('download failure')
        third=await co._async_update_data()
        assert third['calculated_invoice']['estado']=='descarga_factura_no_disponible'
        assert 'acumulado' not in third['calculated_invoice']
        await co._async_update_data()
        assert co.api.get_invoice_pdf.await_count==3  # Bounded retries, no 15-minute PDF loop.
        # Restart from persisted storage at rollover, then receive the last day's curves.
        monkeypatch.setattr(mod.dt_util, 'now', lambda: datetime(2026,11,1,tzinfo=timezone.utc))
        new = mod.NibaCoordinator(hass,SimpleNamespace(entry_id='test',data={'email':'test','password':'test'}))
        new.api=SimpleNamespace(**{k:AsyncMock(return_value=v) for k,v in methods.items()})
        async def days(cups, start, end):
            if start == '2026-10-01':
                return [row(date(2026,10,d)) for d in range(1,32)]
            return []
        new.api.get_consumption_daily.side_effect=days
        result=await new._async_update_data()
        history=result['cycle_history']
        assert history['ciclos_guardados']==2
        assert history['ultimo_cierre']['completo'] is True
        assert history['ultimo_cierre']['dias_valorados']==31
        assert history['ultimo_cierre']['total_al_cierre_eur'] != history['ultimo_cierre']['total_calculado_eur']
        assert result['calculated_invoice']['acumulado']['total_eur']==0
        assert 'private' not in repr(result)
        # A storage failure must fail the update, without committing a fake saved snapshot.
        original=new._history.data.copy()
        monkeypatch.setattr(mod.dt_util, 'now', lambda: datetime(2026,11,2,tzinfo=timezone.utc))
        new._history_store.async_save=AsyncMock(side_effect=OSError('disk full'))
        try:
            await new._async_update_data()
            assert False, 'storage failure should surface'
        except UpdateError as err:
            assert 'historial' in str(err)
        assert new._history.data==original
    asyncio.run(run())
