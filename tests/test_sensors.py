import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace


def test_sensor_setup_preserves_energy_ids_and_adds_separate_totals(monkeypatch):
    def module(name,**attrs):
        m=ModuleType(name)
        for k,v in attrs.items():setattr(m,k,v)
        monkeypatch.setitem(sys.modules,name,m)
    class Entity: pass
    class CoordinatorEntity:
        def __init__(self,c):self.coordinator=c
    module('homeassistant')
    module('homeassistant.components')
    module('homeassistant.components.sensor', SensorEntity=Entity,
           SensorDeviceClass=SimpleNamespace(ENERGY='energy',MONETARY='monetary',POWER='power'),
           SensorStateClass=SimpleNamespace(TOTAL='total',MEASUREMENT='measurement'))
    module('homeassistant.const',CURRENCY_EURO='€',EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic'),
           UnitOfEnergy=SimpleNamespace(KILO_WATT_HOUR='kWh'),UnitOfPower=SimpleNamespace(KILO_WATT='kW'))
    module('homeassistant.helpers',entity_registry=SimpleNamespace(async_get=lambda hass:None,
                                                                  async_entries_for_config_entry=lambda *a:[]))
    module('homeassistant.helpers.update_coordinator',CoordinatorEntity=CoordinatorEntity)
    module('niba_test.coordinator',NibaCoordinator=object)
    sys.modules.pop('niba_test.sensor',None)
    sensor=importlib.import_module('niba_test.sensor')
    co=SimpleNamespace(entry=SimpleNamespace(entry_id='one'),data={
        'calculated_invoice':{'estado':'calculado_provisional','acumulado':{'energia_eur':3.0,'total_eur':9.0},
                              'prevision':{'energia_eur':6.0,'total_eur':18.0}},
        'consumption_api':{'consumption_amount':82.8,'estimated_consumption_amount':116.29}})
    entities=[]
    asyncio.run(sensor.async_setup_entry(SimpleNamespace(data={'niba':{'one':co}}),co.entry,entities.extend))
    by_id={e._attr_unique_id:e for e in entities}
    assert len(by_id)==len(entities)
    assert by_id['niba_cost_period'].native_value==3.0
    assert by_id['niba_invoice_accrued'].native_value==9.0
    assert by_id['niba_estimated_cost'].native_value==6.0
    assert by_id['niba_invoice_forecast'].native_value==18.0
    assert by_id['niba_cost_period_api'].native_value==82.8
    assert by_id['niba_estimated_cost_api'].native_value==116.29
    for entity in entities:
        entity.native_value
        entity.extra_state_attributes
    co.data['calculated_invoice']={'estado':'sin_tarifa_validada'}
    assert by_id['niba_invoice_accrued'].native_value is None
    assert by_id['niba_cost_period'].native_value is None
