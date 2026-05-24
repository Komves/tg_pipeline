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

    result["city"] = _extract_city(raw_text)
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
    # 1. Сначала ищем явную цену в рублях: "Цена: 10 500 000 ₽"
    match = re.search(
        r"(?:цена[:\s]*)?(\d[\d\s]{5,})\s*(?:₽|руб(?:лей)?|р(?![а-я]))",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        digits = re.sub(r"\s+", "", match.group(1))

        try:
            value = int(digits)
        except ValueError:
            value = 0

        if 300_000 <= value <= 500_000_000:
            return f"{value:,}".replace(",", " ")

    # 2. Только если рублёвую цену не нашли — ищем "7.9 млн"
    match = re.search(
        r"\b(\d{1,3}(?:[,.]\d+)?)\s*(млн|миллион|миллиона|миллионов)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1).replace(',', '.')} млн"

    return None

def _extract_area(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв\.?\s*м|метр)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return float(match.group(1).replace(",", "."))

    # Avito URL slug:
    # 2-k._kvartira_924_m_89_et -> 92.4 м²
    match = re.search(
        r"kvartira_(\d{2,4})_m_",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        raw = match.group(1)

        if len(raw) >= 3:
            return float(f"{raw[:-1]}.{raw[-1]}")

        return float(raw)

    return None


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
    if match:
        return int(match.group(1)), int(match.group(2))

    floor = re.search(
        r"этаж[:\s]*(\d{1,2})\b",
        text,
        flags=re.IGNORECASE,
    )
    floors_total = re.search(
        r"этажность[:\s]*(\d{1,2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if floor and floors_total:
        return int(floor.group(1)), int(floors_total.group(1))

    match = re.search(
        r"_m_(\d{2})_et",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        raw = match.group(1)
        return int(raw[0]), int(raw[1])

    return None
def _extract_city(text: str) -> str | None:
    lowered = (text or "").lower()

    avito_city_map = {
        "nizhnevartovsk": "Нижневартовск",
        "novosibirsk": "Новосибирск",
        "moskva": "Москва",
        "sankt-peterburg": "Санкт-Петербург",
        "ekaterinburg": "Екатеринбург",
        "tyumen": "Тюмень",
        "surgut": "Сургут",
        "khanty-mansiysk": "Ханты-Мансийск",
    }

    match = re.search(r"avito\.ru/([^/]+)/", lowered)
    if match:
        slug = match.group(1).strip()
        return avito_city_map.get(slug)

    text_city_markers = {
        "нижневартовск": "Нижневартовск",
        "новосибирск": "Новосибирск",
        "москва": "Москва",
        "санкт-петербург": "Санкт-Петербург",
        "екатеринбург": "Екатеринбург",
        "тюмень": "Тюмень",
        "сургут": "Сургут",
        "ханты-мансийск": "Ханты-Мансийск",
    }

    for marker, city in text_city_markers.items():
        if marker in lowered:
            return city

    return None