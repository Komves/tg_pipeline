from __future__ import annotations

from typing import Any, Dict, List


def build_material_basket(params: Dict[str, Any], surfaces: Dict[str, Any]) -> List[Dict[str, Any]]:
    repair_class = params.get("repair_class") or "middle"

    total_floor = float(surfaces.get("total_floor_area") or 0)
    living_floor = float(surfaces.get("living_floor_area") or 0)
    bathroom_tile = float(surfaces.get("bathroom_tile_area") or 0)
    wall_area = float(surfaces.get("wall_area") or 0)
    plinth_m = float(surfaces.get("plinth_m") or 0)

    basket: List[Dict[str, Any]] = []

    if living_floor > 0:
        basket.append({
            "category": "flooring",
            "name": "Напольное покрытие",
            "query": "ламинат 33 класс" if repair_class != "premium" else "инженерная доска",
            "quantity": round(living_floor * 1.08, 1),
            "unit": "м²",
            "basis": "жилая площадь пола + 8% запас",
        })

    if bathroom_tile > 0:
        basket.append({
            "category": "bathroom_tile",
            "name": "Плитка / керамогранит для санузла",
            "query": "плитка для ванной",
            "quantity": round(bathroom_tile * 1.1, 1),
            "unit": "м²",
            "basis": "площадь плитки санузла + 10% запас",
        })

    if plinth_m > 0:
        basket.append({
            "category": "plinth",
            "name": "Плинтус",
            "query": "плинтус напольный",
            "quantity": round(plinth_m * 1.05, 1),
            "unit": "м",
            "basis": "расчётный периметр + 5% запас",
        })

    if wall_area > 0:
        basket.append({
            "category": "primer",
            "name": "Грунтовка",
            "query": "грунтовка глубокого проникновения",
            "quantity": round(wall_area / 100, 1),
            "unit": "канистра 10 л",
            "basis": "примерно 1 л на 10 м², канистра 10 л",
        })

        basket.append({
            "category": "putty",
            "name": "Шпаклёвка",
            "query": "шпаклевка финишная",
            "quantity": round(wall_area * 1.2, 1),
            "unit": "кг",
            "basis": "примерно 1.2 кг на м² стен",
        })

        basket.append({
            "category": "paint_or_wallpaper",
            "name": "Краска / обои",
            "query": "краска интерьерная моющаяся" if repair_class != "economy" else "обои под покраску",
            "quantity": round(wall_area, 1),
            "unit": "м²",
            "basis": "площадь стен под отделку",
        })

    if total_floor > 0:
        basket.append({
            "category": "rough_mix",
            "name": "Черновые смеси и расходники",
            "query": "ровнитель для пола сухая смесь",
            "quantity": round(total_floor, 1),
            "unit": "м²",
            "basis": "общая площадь пола",
        })

    return basket