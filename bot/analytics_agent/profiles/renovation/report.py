from __future__ import annotations

from typing import Any, Dict


MATERIAL_RATES = {
    "economy": 18000,
    "middle": 28000,
    "premium": 45000,
}


def build_renovation_report(task_id: str, params: Dict[str, Any]) -> str:
    area = params.get("area_m2")
    repair_class = params.get("repair_class") or "middle"
    city = params.get("city") or "город не указан"
    property_type = params.get("property_type") or "тип квартиры не указан"

    missing = params.get("missing") or []

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

    class_title = {
        "economy": "эконом",
        "middle": "средний",
        "premium": "хороший / премиум",
    }.get(repair_class, repair_class)

    type_title = {
        "new_building": "новостройка",
        "secondary": "вторичка",
    }.get(property_type, property_type)

    features = params.get("features") or []
    features_text = ", ".join(features) if features else "не уточнены"

    out = [
        "🧠 Черновая оценка материалов по ремонту",
        "",
        f"ID: {task_id}",
        f"Город: {city}",
        f"Тип: {type_title}",
        f"Площадь: {area:g} м²",
        f"Класс ремонта: {class_title}",
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
        "Статус: это пока нормативная оценка без подключения рыночных цен.",
        "Следующий слой — market pricing collector по строймаркетам.",
    ]

    if missing:
        out.append("")
        out.append("Не хватает для точности:")
        for m in missing:
            out.append(f"• {m}")

    return "\n".join(out)