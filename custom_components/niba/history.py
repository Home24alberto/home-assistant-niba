"""Persistent cycle journal. No credentials, CUPS, addresses or documents."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from .billing import as_date


def supply_key(cups: str) -> str:
    return sha256(cups.encode()).hexdigest()


class CycleHistory:
    def __init__(self, data=None):
        self.data = deepcopy(data) if isinstance(data, dict) else {"supplies": {}}
        if not isinstance(self.data.get("supplies"), dict):
            raise ValueError("Invalid cycle history")

    def cycles(self, supply):
        return self.data["supplies"].setdefault(supply, {})

    def restore_days(self, supply, start):
        return deepcopy(self.cycles(supply).get(start.isoformat(), {}).get("daily", []))

    def update(self, supply, start, end, today, daily, calculated, contract=None):
        cycles = self.cycles(supply)
        for cycle in cycles.values():
            finish = as_date(cycle.get("end"))
            if finish and finish < today and not cycle.get("closed"):
                cycle["closed"] = True
                cycle["closed_detected_on"] = today.isoformat()
        key = start.isoformat()
        cycle = cycles.setdefault(key, {"start": key, "end": end.isoformat(),
                                      "closed": False, "daily": []})
        if cycle["closed"]:
            return  # Closed snapshots are immutable; late corrections need a separate version.
        cycle["daily"] = deepcopy(daily)
        if contract is not None:
            cycle["contract"] = deepcopy(contract)
        cycle["last_attempt_status"] = calculated.get("estado")
        cycle["last_attempt_on"] = today.isoformat()
        # Never overwrite the last usable calculation with an API/tariff failure.
        if isinstance(calculated.get("acumulado"), dict):
            cycle["calculation"] = deepcopy(calculated)
            cycle["calculated_on"] = today.isoformat()
            cycle["complete"] = (
                calculated.get("dias_valorados") == (end-start).days+1
                and calculated.get("hasta_fecha") == end.isoformat()
            )

    def summary(self, supply, bills):
        result = []
        for key, cycle in sorted(self.cycles(supply).items()):
            original = cycle.get("calculation", {})
            calc = cycle.get("final_calculation", original)
            item = {"inicio": cycle["start"], "fin": cycle["end"],
                    "cerrado": cycle.get("closed", False),
                    "completo": cycle.get("complete", False),
                    "hasta_fecha": calc.get("hasta_fecha"),
                    "dias_valorados": calc.get("dias_valorados", 0),
                    "desglose_calculado": deepcopy(calc.get("acumulado", {})),
                    "total_al_cierre_eur": original.get("acumulado", {}).get("total_eur"),
                    "total_calculado_eur": calc.get("acumulado", {}).get("total_eur"),
                    "ultimo_estado": cycle.get("last_attempt_status"),
                    "estado_comparacion": "pendiente_factura"}
            matches = [b for b in bills if str(b.get("start_at")) == cycle["start"]
                       and str(b.get("end_at")) == cycle["end"]]
            # Multiple documents may be corrections: do not silently pick a total.
            if len(matches) > 1:
                item["estado_comparacion"] = "varias_facturas_revisar"
            elif matches:
                b = matches[0]
                value = b.get("total_amount")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    item["total_facturado_eur"] = value
                    if item["completo"] and item["total_calculado_eur"] is not None:
                        item["diferencia_eur"] = round(value-item["total_calculado_eur"], 2)
                        item["estado_comparacion"] = "comparacion_provisional_revisar_conceptos"
                    else:
                        item["estado_comparacion"] = "calculo_incompleto"
            result.append(item)
        closed = [x for x in result if x["cerrado"]]
        return {"ciclos_guardados": len(result), "ciclos_cerrados": len(closed),
                "ultimo_cierre": closed[-1] if closed else {}, "ciclos": result}
