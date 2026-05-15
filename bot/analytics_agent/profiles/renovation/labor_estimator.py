from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

from analytics_agent.profiles.renovation.surface_estimator import estimate_surfaces


LABOR_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "labor_cache.json"


LABOR_WORKS = [
    {
        "key": "floor_leveling",
        "quantity_field": "total_floor_area",
    },
    {
        "key": "laminate_install",
        "quantity_field": "living_floor_area",
    },
    {
        "key": "tile_install",
        "quantity_field": "bathroom_tile_area",
    },
    {
        "key": "wall_primer",
        "quantity_field": "wall_area",
    },
    {
        "key": "wall_putty",
        "quantity_field": "wall_area",
    },
    {
        "key": "wall_paint",
        "quantity_field": "wall_area",
    },
    {
        "key": "baseboard_install",
        "quantity_field": "plinth_m",
    },
]


def _load_labor_cache() -> Dict[str, Any]:
    if not LABOR_CACHE_PATH.exists():
        return {}

    try:
        data = json.loads(LABOR_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    items = data.get("items")
    if not isinstance(items, dict):
        return {}

    return items


def estimate_labor(params: Dict) -> Dict:
    surfaces = estimate_surfaces(params)
    cache_items = _load_labor_cache()

    labor_items = []

    for work in LABOR_WORKS:
        key = work["key"]
        quantity_field = work["quantity_field"]

        market_item = cache_items.get(key) or {}
        if market_item.get("status") != "ok":
            continue

        unit_price = market_item.get("median_price_rub")
        if unit_price is None:
            continue

        quantity = float(surfaces.get(quantity_field) or 0)
        if quantity <= 0:
            continue

        total = int(round(quantity * float(unit_price)))

        labor_items.append(
            {
                "key": key,
                "title_ru": market_item.get("title_ru") or market_item.get("title") or key,
                "quantity": round(quantity, 1),
                "unit": market_item.get("unit") or "",
                "unit_price_rub": int(float(unit_price)),
                "total_price_rub": total,
                "source": market_item.get("source") or "labor_cache",
                "source_url": market_item.get("source_url"),
            }
        )

    labor_total = sum(int(item["total_price_rub"]) for item in labor_items)

    return {
        "labor_base": labor_total,
        "labor_total": labor_total,
        "labor_low": int(labor_total * 0.85),
        "labor_high": int(labor_total * 1.25),
        "labor_items": labor_items,
        "labor_items_count": len(labor_items),
        "labor_cache_path": str(LABOR_CACHE_PATH),
        "labor_note": "Работы рассчитаны по labor_cache.json: объем × медианная рыночная ставка.",
    }
        