from __future__ import annotations

import re
from typing import Any


def parse_real_estate_object(text: str) -> dict[str, Any]:
    raw_text = text or ""

    result: dict[str, Any] = {
        "raw_text": raw_text,
        "city": None,
        "price_text": None,
        "rooms": None,
        "area_m2": None,
        "floor": None,
        "floors_total": None,
        "invest_mode": _detect_invest_mode(raw_text),
    }

    result["price_text"] = _extract_price(raw_text)
    result["area_m2"] = _extract_area(raw_text)
    result["rooms"] = _extract_rooms(raw_text)

    floor_pair = _extract_floor_pair(raw_text)
    if floor_pair:
        result["floor"], result["floors_total"] = floor_pair

    return result


def _detect_invest_mode(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "под аренду",
        "инвестиция",
        "как инвестиция",
        "окупаемость",
        "сдавать",
        "аренда",
    )
    return any(marker in lowered for marker in markers)


def _extract_price(text: str) -> str | None:
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(млн|миллион|миллиона|миллионов)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1).replace(',', '.')} млн"

    match = re.search(
        r"(\d[\d\s]{5,})\s*(?:₽|руб|р)?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    return None


def _extract_area(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв\.?\s*м|метр)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    return float(match.group(1).replace(",", "."))


def _extract_rooms(text: str) -> str | None:
    match = re.search(
        r"\b([1-5])\s*[- ]?\s*(?:к|комн|комнат)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1)}к"

    return None


def _extract_floor_pair(text: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", text)
    if not match:
        return None

    return int(match.group(1)), int(match.group(2))