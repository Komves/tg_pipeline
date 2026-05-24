from __future__ import annotations

from typing import Any


def build_real_estate_report(
    data: dict[str, Any],
    analysis: dict[str, Any],
    market: dict[str, Any],
) -> str:
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

    if market.get("market_found"):
        lines.append(f"- ориентир рынка: {market.get('market_range')}")

        if market.get("market_price_per_m2"):
            lines.append(
                f"- рынок за м²: ~{market.get('market_price_per_m2')}"
            )

        lines.append(f"- статус: {market.get('market_status')}")

        if market.get("liquidity"):
            lines.append(
                f"- ликвидность: {market.get('liquidity')}"
            )

        if market.get("investment_score"):
            lines.append(
                f"- инвестиционно: {market.get('investment_score')}"
            )

        lines.append(f"- вывод: {market.get('market_comment')}")

        if market.get("bargain_range"):
            lines.append(
                f"- торг: ~{market.get('bargain_range')}"
            )

        elif market.get("bargain_comment"):
            lines.append(
                f"- торг: {market.get('bargain_comment')}"
            )
    else:
        lines.append(f"- {market.get('market_comment')}")

    if data.get("invest_mode"):
        lines.append("")
        lines.append("💰 Инвестиция")
        rent_range = market.get("rent_range")

        if rent_range:
            lines.append(f"- аренда: ~{rent_range}")
        else:
            lines.append("- аренда: нет rough-оценки для этого города")

        lines.append("- окупаемость: rough MVP-оценка")

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
    verdict = (
        market.get("watch_decision")
        or analysis.get("verdict")
        or "нужны данные для вывода"
    )

    lines.append(f"- {verdict}")

    return "\n".join(lines)


def _missing_required_fields(data: dict[str, Any]) -> list[str]:
    questions: list[str] = []

    if not data.get("price_text"):
        questions.append("Не вижу цену.")

    if not data.get("area_m2"):
        questions.append("Не вижу метраж.")

    return questions