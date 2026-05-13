from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import requests


def _extract_price_rub(text: str) -> int | None:
    s = (text or "").replace("\u00a0", " ")
    candidates = []

    for m in re.finditer(r"(\d[\d\s]{1,10})\s*(?:₽|руб\.?|р\.)", s, flags=re.I):
        raw = re.sub(r"\D+", "", m.group(1) or "")
        if not raw:
            continue

        try:
            value = int(raw)
        except Exception:
            continue

        if 10 <= value <= 2_000_000:
            candidates.append(value)

    if not candidates:
        return None

    return sorted(candidates)[0]

TRUSTED_SOURCES = [
    ("petrovich.ru", "Петрович"),
    ("leroymerlin.ru", "Лемана"),
    ("vseinstrumenti.ru", "ВсеИнструменты"),
    ("maxidom.ru", "Максидом"),
]


def _text_has_expected_unit(text: str, item: Dict[str, Any]) -> bool:
    s = (text or "").lower()
    expected = str(item.get("market_unit") or item.get("unit") or "").lower()

    if expected in ("м²", "м2"):
        return any(x in s for x in ("м²", "м2", "кв.м", "кв. м", "за м²", "за м2"))

    if expected == "м":
        return any(x in s for x in ("за м", "пог.м", "пог. м", "метр"))

    if expected in ("мешок", "канистра", "ведро", "упаковка", "шт"):
        return expected in s or "шт" in s or "упак" in s

    return False


def _search_price(query: str, city: str, item: Dict[str, Any]) -> Dict[str, Any]:
    api_key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "reason": "BRAVE_SEARCH_API_KEY is not set",
        }

    best_rejected = None

    for domain, source_name in TRUSTED_SOURCES:
        q = f"{query} цена {city} site:{domain}".strip()

        try:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                params={
                    "q": q,
                    "count": 5,
                    "search_lang": "ru",
                    "country": "RU",
                },
                timeout=12,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            continue

        results = (data.get("web") or {}).get("results") or []

        for found_item in results:
            title = (found_item.get("title") or "").strip()
            desc = (found_item.get("description") or "").strip()
            url = (found_item.get("url") or "").strip()
            text = f"{title}\n{desc}"

            price = _extract_price_rub(text)
            if not price:
                continue

            rejected = {
                "status": "unusable",
                "query": q,
                "unit_price": price,
                "source_title": title,
                "source_url": url,
                "source": source_name,
                "reason": "unit not confirmed",
            }

            if best_rejected is None:
                best_rejected = rejected

            if not _text_has_expected_unit(text, item):
                continue

            return {
                "status": "ok",
                "query": q,
                "unit_price": price,
                "source_title": title,
                "source_url": url,
                "source": source_name,
            }

    if best_rejected:
        return best_rejected

    return {
        "status": "unavailable",
        "query": query,
        "reason": "trusted source price not found",
    }

def price_material_basket(
    basket: List[Dict[str, Any]],
    *,
    city: str,
) -> Dict[str, Any]:
    priced_items = []
    total = 0

    for item in basket or []:
        query = str(item.get("query") or item.get("name") or "").strip()
        quantity = float(item.get("quantity") or 0)
        required_packs = int(item.get("required_packs") or 0)

        if not query or quantity <= 0:
            continue

        found = _search_price(query, city, item)

        row = dict(item)
        row["pricing_status"] = found.get("status")
        row["pricing_source"] = found.get("source")
        row["pricing_query"] = found.get("query")
        row["pricing_reason"] = found.get("reason")
        row["source_title"] = found.get("source_title")
        row["source_url"] = found.get("source_url")

        if found.get("status") == "ok":
            unit_price = int(found.get("unit_price") or 0)
            row["unit_price_rub"] = unit_price

            pricing_mode = str(item.get("pricing_mode") or "by_quantity")

            if pricing_mode == "by_pack" and required_packs > 0:
                row["total_price_rub"] = int(round(required_packs * unit_price))
            else:
                row["total_price_rub"] = int(round(quantity * unit_price))

            row["usable_for_total"] = True
            total += row["total_price_rub"]
        else:
            row["usable_for_total"] = False

        priced_items.append(row)

    ok_count = sum(1 for x in priced_items if x.get("pricing_status") == "ok")

    return {
        "status": "ok" if ok_count else "unavailable",
        "priced_count": ok_count,
        "items_count": len(priced_items),
        "total_price_rub": total if ok_count else None,
        "items": priced_items,
    }