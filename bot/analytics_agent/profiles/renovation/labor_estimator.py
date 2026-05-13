from __future__ import annotations

from typing import Dict


LABOR_RATES = {
    "economy": 14000,
    "middle": 22000,
    "premium": 38000,
}


def estimate_labor(params: Dict) -> Dict:
    area = float(params.get("area_m2") or 0)

    repair_class = params.get("repair_class") or "middle"

    base_rate = LABOR_RATES.get(
        repair_class,
        LABOR_RATES["middle"],
    )

    base = int(area * base_rate)

    return {
        "labor_base": base,
        "labor_low": int(base * 0.85),
        "labor_high": int(base * 1.25),
    }