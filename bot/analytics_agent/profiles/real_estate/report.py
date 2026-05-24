from __future__ import annotations

from typing import Any


def build_real_estate_report(data: dict[str, Any], analysis: dict[str, Any]) -> str:
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
    lines.append("📊 Быстрая оценка")

    if analysis.get("price_per_m2"):
        lines.append(f"- цена за м²: ~{analysis['price_per_m2']:,} ₽".replace(",", " "))
    else:
        lines.append("- цена за м²: не рассчитана")

    lines.append("- рынок: нужен web-сравнительный этап")
    lines.append("- статус цены: без сравнения с аналогами не подтверждён")

    if data.get("invest_mode"):
        lines.append("")
        lines.append("💰 Инвестиция")
        lines.append("- аренда: нужен web-сравнительный этап")
        lines.append("- окупаемость: считается после ориентира аренды")

    lines.append("")
    lines.append("⚠️ Риски")

    risks = analysis.get("risks") or []
    if risks:
        for risk in risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- явные базовые риски по введённым данным не выделены")

    lines.append("- юридическая проверка, ЕГРН и история переходов в MVP не выполняются")
    questions = analysis.get("questions") or []
    if questions:
        lines.append("")
        lines.append("❓ Что уточнить")
        for question in questions:
            lines.append(f"- {question}")

    lines.append("")
    lines.append("🧠 Вердикт Веси")
    lines.append(f"- {analysis.get('verdict') or 'нужны данные для вывода'}")

    return "\n".join(lines)


def _missing_required_fields(data: dict[str, Any]) -> list[str]:
    questions: list[str] = []

    if not data.get("price_text"):
        questions.append("Не вижу цену.")

    if not data.get("area_m2"):
        questions.append("Не вижу метраж.")

    return questions