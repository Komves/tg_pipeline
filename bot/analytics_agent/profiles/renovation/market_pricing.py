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


def _search_price(query: str, city: str) -> Dict[str, Any]:
    api_key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "reason": "BRAVE_SEARCH_API_KEY is not set",
        }

    q = f"{query} цена {city}".strip()

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
        return {
            "status": "unavailable",
            "reason": f"{type(e).__name__}: {e}",
        }

    results = (data.get("web") or {}).get("results") or []

    for item in results:
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or "").strip()
        url = (item.get("url") or "").strip()

        price = _extract_price_rub(f"{title}\n{desc}")

        if price:
            return {
                "status": "ok",
                "query": q,
                "unit_price": price,
                "source_title": title,
                "source_url": url,
                "source": "Brave Search snippet",
            }

    return {
        "status": "unavailable",
        "query": q,
        "reason": "price not found in search snippets",
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

        found = _search_price(query, city)

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

            if required_packs > 0:
                row["total_price_rub"] = int(round(required_packs * unit_price))
            else:
                row["total_price_rub"] = int(round(quantity * unit_price))

            total += row["total_price_rub"]

        priced_items.append(row)

    ok_count = sum(1 for x in priced_items if x.get("pricing_status") == "ok")

    return {
        "status": "ok" if ok_count else "unavailable",
        "priced_count": ok_count,
        "items_count": len(priced_items),
        "total_price_rub": total if ok_count else None,
        "items": priced_items,
    }