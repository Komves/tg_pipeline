from __future__ import annotations

from typing import Any


def build_real_estate_report(data: dict[str, Any]) -> str:
    missing = _missing_required_fields(data)
    if missing:
        return "\n".join(missing)

    lines: list[str] = []

    lines.append("🏠 Объект")
    if data.get("rooms"):
        lines.append(f"- {data['rooms']}")
    if data.get("area_m2"):
        lines.append(f"- {data['area_m2']} м²")
    if data.get("floor") and data.get("floors_total"):
        lines.append(f"- {data['floor']}/{data['floors_total']}")
    if data.get("price_text"):
        lines.append(f"- цена: {data['price_text']}")

    lines.append("")
    lines.append("📊 Рынок")
    lines.append("- ориентир рынка: нужен web-сравнительный этап")
    lines.append("- статус цены: предварительно без рыночной проверки")

    if data.get("invest_mode"):
        lines.append("")
        lines.append("💰 Инвестиция")
        lines.append("- аренда: нужен web-сравнительный этап")
        lines.append("- окупаемость: считается после ориентира аренды")

    lines.append("")
    lines.append("⚠️ Риски")
    lines.append("- юридическая проверка, ЕГРН и история переходов в MVP не выполняются")
    lines.append("- рыночные риски будут уточняться после web-сравнения")

    lines.append("")
    lines.append("🧠 Вердикт Веси")
    lines.append("- объект можно предварительно разобрать")
    lines.append("- для вывода по цене нужен следующий этап: web market comparison")

    return "\n".join(lines)


def _missing_required_fields(data: dict[str, Any]) -> list[str]:
    questions: list[str] = []

    if not data.get("price_text"):
        questions.append("Не вижу цену.")

    if not data.get("area_m2"):
        questions.append("Не вижу метраж.")

    return questions