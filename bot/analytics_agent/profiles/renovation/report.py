from __future__ import annotations

from typing import Any, Dict

from analytics_agent.profiles.renovation.surface_estimator import estimate_surfaces

from analytics_agent.profiles.renovation.labor_estimator import estimate_labor
from analytics_agent.profiles.renovation.material_basket import build_material_basket
from analytics_agent.profiles.renovation.market_pricing import price_material_basket


MATERIAL_RATES = {
    "economy": 18000,
    "middle": 28000,
    "premium": 45000,
}


def build_renovation_report(task_id: str, params: Dict[str, Any]) -> str:
    area = params.get("area_m2")
    repair_class_raw = params.get("repair_class")
    repair_class = repair_class_raw or "middle"
    ceiling_height = params.get("ceiling_height") or 2.7
    city = params.get("city") or "город не указан"
    property_type = params.get("property_type") or "тип квартиры не указан"

    missing = [
        m for m in list(params.get("missing") or [])
        if (
            (m == "area_m2" and not params.get("area_m2"))
            or (m == "city" and not params.get("city"))
            or (m == "repair_class" and not params.get("repair_class"))
            or (m == "property_type" and not params.get("property_type"))
            or (m == "ceiling_height" and not params.get("ceiling_height"))
            or (m == "estimate_scope" and not params.get("estimate_scope"))
        )
    ]

    if not area:
        return (
            "🧠 Аналитическая задача принята: ремонт квартир\n\n"
            f"ID: {task_id}\n"
            "Но не вижу площадь квартиры.\n"
            "Нужно минимум: город, площадь, класс ремонта."
        )

    base_rate = MATERIAL_RATES.get(repair_class, MATERIAL_RATES["middle"])
    base = int(float(area) * base_rate)

    low = int(base * 0.85)
    high = int(base * 1.25)
    reserve = int(base * 0.15)

    surfaces = estimate_surfaces(params)
    material_basket = build_material_basket(params, surfaces)
    priced_basket = price_material_basket(
        material_basket,
        city=str(city),
    )

    estimate_scope = params.get("estimate_scope") or "materials"

    labor = None

    if estimate_scope == "materials_and_labor":
        labor = estimate_labor(params)
    class_title = {
        "economy": "эконом",
        "middle": "средний",
        "premium": "хороший / премиум",
    }.get(repair_class, repair_class)

    type_title = {
        "new_building": "новостройка",
        "secondary": "вторичка",
        "one_room_apartment": "однокомнатная квартира",
        "two_room_apartment": "двухкомнатная квартира",
        "multi_room_apartment": "многокомнатная квартира",
    }.get(property_type, property_type)

    features = params.get("features") or []
    features_text = ", ".join(features) if features else "не уточнены"

    layout = params.get("layout") if isinstance(params.get("layout"), dict) else None

    out = [
        "🧠 Черновая оценка материалов по ремонту",
        "",
        f"ID: {task_id}",
        f"Город: {city}",
        f"Тип: {type_title}",
        f"Площадь: {area:g} м²",
        f"Класс ремонта: {class_title}" + ("" if repair_class_raw else " (по умолчанию)"),
        f"Высота потолков: {ceiling_height} м",
        f"Особенности: {features_text}",
        "",
        "Оценка материалов:",
        f"• нижняя граница: {low:,} ₽".replace(",", " "),
        f"• реалистично: {base:,} ₽".replace(",", " "),
        f"• с запасом: {high:,} ₽".replace(",", " "),
        f"• резерв на перерасход: ~{reserve:,} ₽".replace(",", " "),
        "",
        "Что входит в грубую модель:",
        "• черновые смеси, грунтовки, шпаклёвки;",
        "• напольные покрытия;",
        "• плитка/клей/затирка;",
        "• электрика;",
        "• сантехнические материалы;",
        "• краска/обои/расходники;",
        "• двери и базовая фурнитура — если явно не исключены.",
        "",
        "Статус: рыночная прайс-проверка выполняется по каталогу Лемана ПРО.",
        "Цены являются ориентировочными и зависят от региона, бренда и наличия.",
        "",
        f"Режим расчета: {'материалы + работы' if estimate_scope == 'materials_and_labor' else 'только материалы'}",
    ]

    if layout:
        out.append("")
        out.append("Данные из плана квартиры:")
        out.append(f"• статус распознавания: {layout.get('status', '-')}")
        if layout.get("total_area_m2"):
            out.append(f"• площадь по плану: {layout.get('total_area_m2')} м²")
        if layout.get("bathrooms") is not None:
            out.append(f"• санузлы по плану: {layout.get('bathrooms')}")
        if layout.get("balcony") is not None:
            out.append(f"• балкон/лоджия: {'да' if layout.get('balcony') else 'нет'}")
        if layout.get("doors_count") is not None:
            out.append(f"• дверей ориентировочно: {layout.get('doors_count')}")

        rooms = layout.get("rooms") or []
        if rooms:
            out.append("• помещения:")
            for r in rooms[:12]:
                name = r.get("name") or "помещение"
                area_r = r.get("area_m2")
                if area_r:
                    out.append(f"  - {name}: {area_r} м²")
                else:
                    out.append(f"  - {name}: площадь не прочитана")
    

    if labor:
        total_low = low + labor["labor_low"]
        total_base = base + labor["labor_base"]
        total_high = high + labor["labor_high"]

        out.extend([
            "",
            "Оценка работ:",
            f"• нижняя граница: {labor['labor_low']:,} ₽".replace(",", " "),
            f"• реалистично: {labor['labor_base']:,} ₽".replace(",", " "),
            f"• с запасом: {labor['labor_high']:,} ₽".replace(",", " "),
            "",
            "Материалы + работы:",
            f"• нижняя граница: {total_low:,} ₽".replace(",", " "),
            f"• реалистично: {total_base:,} ₽".replace(",", " "),
            f"• с запасом: {total_high:,} ₽".replace(",", " "),
        ])

    if surfaces:
        out.extend([
            "",
            "Предварительные объемы:",
            f"• полы: ~{surfaces.get('total_floor_area', 0)} м²",
            f"• потолки: ~{surfaces.get('ceiling_area', 0)} м²",
            f"• стены под отделку: ~{surfaces.get('wall_area', 0)} м²",
            f"  (площадь × высота потолков {ceiling_height} м × коэффициент стен)",
            f"• плитка санузла: ~{surfaces.get('bathroom_tile_area', 0)} м²",
            f"• плинтус: ~{surfaces.get('plinth_m', 0)} м",
        ])

    if material_basket:
        out.append("")
        out.append("Черновая корзина материалов:")
        for item in material_basket:
            out.append(
                f"• {item['name']}: ~{item['quantity']} {item['unit']}"
                f" ({item['basis']})"
                + (
                    f", примерно {item['required_packs']} {item['market_unit']}"
                    if item.get("required_packs")
                    else ""
                )
            )

    if priced_basket.get("items"):
        out.append("")
        "Прайс-проверка по каталогу Лемана ПРО:"
        for item in priced_basket.get("items") or []:
            if not item.get("usable_for_total"):
                continue

            out.append(
                f"• {item['name']}: ~{item['unit_price_rub']:,} ₽ за {item.get('market_unit') or item['unit']}, "
                f"итого ~{item['total_price_rub']:,} ₽".replace(",", " ")
            )

            if item.get("source_title"):
                out.append(f"  источник: {item['source_title']}")

        total_market = priced_basket.get("total_price_rub")

        if total_market:
            out.append(f"Итого по подтверждённым рыночным позициям: ~{total_market:,} ₽".replace(",", " "))
        else:
            out.append("Рыночные цены пока недостаточно надёжны для включения в расчёт.")
    else:
        out.append("")
        out.append("Рыночные цены: не найдены или не подключён BRAVE_SEARCH_API_KEY.")

    if missing:
        field_labels = {
            "area_m2": "площадь квартиры",
            "city": "город",
            "repair_class": "класс ремонта",
            "property_type": "тип объекта",
            "ceiling_height": "высота потолков",
            "estimate_scope": "режим расчета",
        }

        missing_text = ", ".join(field_labels.get(m, m) for m in missing)

        out.append("")
        out.append(f"Не хватает для точности: {missing_text}.")

    return "\n".join(out)

def build_renovation_followup(task_id: str, params: Dict[str, Any], question: str) -> str:
    q = (question or "").lower()

    area = params.get("area_m2")
    repair_class_raw = params.get("repair_class")
    repair_class = repair_class_raw or "middle"
    rate = MATERIAL_RATES.get(repair_class, MATERIAL_RATES["middle"])

    if "источник" in q or "откуда" in q or "почему" in q:
        return (
            "По источникам сейчас честно:\n\n"
            f"ID: {task_id}\n"
            "Рыночные источники ещё не подключены.\n"
            "Текущий расчёт — нормативная модель.\n\n"
            f"Формула сейчас: площадь × ставка материалов.\n"
            f"Площадь: {area or 'не указана'} м²\n"
            f"Ставка класса ремонта: {rate:,} ₽/м²\n".replace(",", " ")
            + "\nДальше нужно подключать pricing collector: Лемана / Петрович / Ozon / Яндекс Маркет."
        )

    return (
        "По текущей задаче могу уточнять расчёт, добавлять параметры и пересчитывать.\n\n"
        f"ID: {task_id}\n"
        "Примеры:\n"
        "• добавь 2 санузла\n"
        "• учти ламинат и плитку в санузле\n"
        "• загружу план квартиры\n"
        "• подробнее по источникам"
    )