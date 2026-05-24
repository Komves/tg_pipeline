from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import requests


BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def enrich_listing_data(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("price_text"):
        return data

    raw_text = _compact(str(data.get("raw_text") or ""))

    if "avito.ru" not in raw_text.lower():
        return data

    listing_id = _extract_avito_listing_id(raw_text)
    if not listing_id:
        return data

    rows = _search_listing_by_id(listing_id)

    price_text = _extract_price_from_listing_results(rows, listing_id)
    if price_text:
        data["price_text"] = price_text

    return data


def _extract_avito_listing_id(text: str) -> str | None:
    match = re.search(r"_(\d{7,12})(?:\?|$)", text)
    if match:
        return match.group(1)

    return None


def _search_listing_by_id(listing_id: str) -> List[Dict[str, str]]:
    if not BRAVE_SEARCH_API_KEY:
        return []

    query = f"site:avito.ru {listing_id}"

    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
        },
        params={
            "q": query,
            "count": 5,
            "search_lang": "ru",
            "country": "RU",
        },
        timeout=20,
    )
    r.raise_for_status()

    rows: List[Dict[str, str]] = []

    for item in (r.json().get("web") or {}).get("results") or []:
        rows.append({
            "title": _compact(item.get("title") or ""),
            "url": _compact(item.get("url") or ""),
            "description": _compact(item.get("description") or ""),
        })

    return rows


def _extract_price_from_listing_results(
    rows: List[Dict[str, str]],
    listing_id: str,
) -> str | None:
    for row in rows:
        url = row.get("url") or ""

        if listing_id not in url:
            continue

        text = _compact(
            (row.get("title") or "")
            + " "
            + (row.get("description") or "")
        )

        price = _extract_price_from_text(text)
        if price:
            return price

    return None


def _extract_price_from_text(text: str) -> str | None:
    match = re.search(
        r"(\d[\d\s]{5,})\s*(?:₽|руб)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    digits = re.sub(r"\s+", "", match.group(1))

    try:
        value = int(digits)
    except ValueError:
        return None

    if value < 300_000 or value > 500_000_000:
        return None

    return f"{value:,}".replace(",", " ")