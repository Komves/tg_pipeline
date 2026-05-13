from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import requests
from pathlib import Path

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
    ("lemanapro.ru", "Лемана ПРО"),
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
    search_url = (
        "https://lemanapro.ru/search/"
        f"?q={requests.utils.quote(query)}"
    )

    try:
        r = requests.get(
            search_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                )
            },
            timeout=15,
        )

        r.raise_for_status()

        html = r.text or ""

        try:
            debug_dir = Path(os.getenv("DATA_DIR", "/data")) / "market_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_query = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_-]+", "_", query)[:80]
            (debug_dir / f"lemanapro_{safe_query}.html").write_text(
                html[:300_000],
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[lemanapro] debug html save failed: {type(e).__name__}: {e}", flush=True)

        print(
            "[lemanapro] "
            f"query={query!r} "
            f"url={search_url!r} "
            f"status={r.status_code} "
            f"html_len={len(html)}",
            flush=True,
        )

    except Exception as e:
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"lemanapro request failed: {e}",
        }

    prices = []

    for m in re.finditer(
        r'(\d[\d\s]{1,10})\s*₽',
        html,
        flags=re.I,
    ):
        raw = re.sub(r"\D+", "", m.group(1) or "")

        if not raw:
            continue

        try:
            value = int(raw)
        except Exception:
            continue

        if 50 <= value <= 500_000:
            prices.append(value)

    print(
        "[lemanapro] "
        f"query={query!r} "
        f"prices_count={len(prices)} "
        f"prices_sample={prices[:10]}",
        flush=True,
    )

    if not prices:
        return {
            "status": "unavailable",
            "query": query,
            "reason": "no catalog prices found in lemanapro html",
        }

    prices = sorted(prices)

    median_price = prices[min(len(prices) // 2, len(prices) - 1)]

    return {
        "status": "ok",
        "query": query,
        "unit_price": median_price,
        "source_title": "Каталог Лемана ПРО",
        "source_url": search_url,
        "source": "Лемана ПРО",
    }

def price_material_basket(
    basket_items: List[Dict[str, Any]],
    city: str,
) -> List[Dict[str, Any]]:

    print(
        "[PRICING] entered price_material_basket",
        flush=True,
    )
    priced_items = []
    total = 0

    for item in basket_items or []:
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