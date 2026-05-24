from __future__ import annotations

from typing import Any


def analyze_real_estate(data: dict[str, Any]) -> dict[str, Any]:
    price_rub = _price_to_rub(data.get("price_text"))
    area_m2 = data.get("area_m2")

    price_per_m2 = None
    if price_rub and area_m2:
        price_per_m2 = round(price_rub / float(area_m2))

    risks: list[str] = []
    positives: list[str] = []
    questions: list[str] = []

    floor = data.get("floor")
    floors_total = data.get("floors_total")

    if floor == 1:
        risks.append("первый этаж — ниже ликвидность и выше бытовые риски")

    if floors_total and floor == floors_total:
        risks.append("последний этаж — нужно проверять крышу и протечки")

    if area_m2 and area_m2 >= 80:
        positives.append("большая площадь — объект интересен для семьи")
        risks.append("большая площадь снижает круг покупателей при перепродаже")

    if not data.get("city"):
        questions.append("Не вижу город.")

    if not data.get("price_text"):
        questions.append("Не вижу цену.")

    if not area_m2:
        questions.append("Не вижу метраж.")

    if not floor or not floors_total:
        questions.append("Не вижу этаж / этажность.")

    verdict = "можно смотреть, но вывод по цене нужен после сравнения с рынком"
    if price_per_m2:
        verdict = "смотреть можно, но сначала сравнить цену за м² с аналогами"

    return {
        "price_rub": price_rub,
        "price_per_m2": price_per_m2,
        "risks": risks,
        "positives": positives,
        "questions": questions,
        "verdict": verdict,
    }


def _price_to_rub(price_text: str | None) -> int | None:
    if not price_text:
        return None

    text = str(price_text).replace(",", ".").replace(" ", "").lower()

    if "млн" in text:
        number = text.replace("млн", "")
        try:
            return int(float(number) * 1_000_000)
        except ValueError:
            return None

    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None

    return int(digits)