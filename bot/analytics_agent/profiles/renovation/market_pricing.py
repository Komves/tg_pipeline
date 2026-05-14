from __future__ import annotations

import json
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


def _market_cache_path() -> Path:
    return Path(os.getenv("DATA_DIR", ".")) / "analytics_agent" / "data" / "market_cache.json"


def _load_market_cache() -> Dict[str, Any]:
    path = _market_cache_path()

    try:
        print(
            f"[MARKET_CACHE_CHECK] path={str(path)!r} exists={path.exists()}",
            flush=True,
        )

        if not path.exists():
            return {}

        cache = json.loads(path.read_text(encoding="utf-8"))
        items = cache.get("items") or {}

        print(
            "[MARKET_CACHE_LOADED] "
            f"schema={cache.get('schema')!r} "
            f"updated_at={cache.get('updated_at')!r} "
            f"items_count={len(items)} "
            f"items_keys={list(items.keys())!r}",
            flush=True,
        )

        return cache

    except Exception as e:
        print(
            f"[MARKET_CACHE_LOAD_ERROR] path={str(path)!r} error={type(e).__name__}: {e}",
            flush=True,
        )
        return {}

def _price_from_market_cache(query: str, city: str, item: Dict[str, Any]) -> Dict[str, Any]:
    cache = _load_market_cache()
    items = cache.get("items") or {}

    category = str(item.get("category") or "").strip()
    cached = items.get(category)

    if not cached:
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"market cache miss for category={category!r}",
        }

    if cached.get("status") != "ok":
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"market cache item unavailable for category={category!r}",
            "source": cached.get("source"),
            "source_title": cached.get("title"),
            "source_url": cached.get("source_url"),
            "market_updated_at": cache.get("updated_at"),
        }

    unit_price = cached.get("median_price_rub")

    if not unit_price:
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"market cache item has no median_price_rub for category={category!r}",
            "source": cached.get("source"),
            "source_title": cached.get("title"),
            "source_url": cached.get("source_url"),
            "market_updated_at": cache.get("updated_at"),
        }

    return {
        "status": "ok",
        "query": cached.get("query") or query,
        "unit_price": int(unit_price),
        "source": cached.get("source") or cache.get("source"),
        "source_title": cached.get("title"),
        "source_url": cached.get("source_url"),
        "market_updated_at": cache.get("updated_at"),
        "market_city": cache.get("city"),
        "market_schema": cache.get("schema"),
    }


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
    print(
        "[LEMANAPRO_SEARCH_START] "
        f"query={query!r} "
        f"city={city!r} "
        f"item_name={item.get('name')!r} "
        f"category={item.get('category')!r} "
        f"expected_unit={item.get('market_unit')!r}",
        flush=True,
    )

    search_url = (
        "https://lemanapro.ru/search/"
        f"?q={requests.utils.quote(query)}"
    )

    print(
        f"[LEMANAPRO_SEARCH_URL] {search_url}",
        flush=True,
    )

    try:
        print("[LEMANAPRO_HTTP_BEFORE]", flush=True)

        r = requests.get(
            search_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            timeout=15,
        )

        print(
            "[LEMANAPRO_HTTP_AFTER] "
            f"status={r.status_code} "
            f"content_type={r.headers.get('content-type')!r} "
            f"final_url={r.url!r}",
            flush=True,
        )

        r.raise_for_status()

        html = r.text or ""

        print(
            "[LEMANAPRO_HTML] "
            f"len={len(html)} "
            f"has_ruble={'₽' in html} "
            f"has_query={query.lower() in html.lower()} "
            f"head={html[:200].replace(chr(10), ' ')!r}",
            flush=True,
        )

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
        print(
            f"[LEMANAPRO_ERROR] {type(e).__name__}: {e}",
            flush=True,
        )
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"lemanapro request failed: {type(e).__name__}: {e}",
        }

    prices = []

    print(
        "[LEMANAPRO_PRICE_SCAN_START]",
        flush=True,
    )

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
        print(
            "[LEMANAPRO_NO_PRICES] "
            f"query={query!r}",
            flush=True,
        )
        return {
            "status": "unavailable",
            "query": query,
            "reason": "no catalog prices found in lemanapro html",
        }

    prices = sorted(prices)

    median_price = prices[min(len(prices) // 2, len(prices) - 1)]

    print(
        "[LEMANAPRO_SEARCH_OK] "
        f"query={query!r} "
        f"median_price={median_price} "
        f"prices_count={len(prices)}",
        flush=True,
    )

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

        print(
            "[PRICING_ITEM] "
            f"name={item.get('name')!r} "
            f"query={item.get('query')!r} "
            f"quantity={item.get('quantity')!r}",
            flush=True,
        )
        query = str(item.get("query") or item.get("name") or "").strip()
        quantity = float(item.get("quantity") or 0)
        required_packs = int(item.get("required_packs") or 0)

        if not query or quantity <= 0:
            print(
                "[PRICING_SKIP] "
                f"query={query!r} "
                f"quantity={quantity!r}",
                flush=True,
            )
            continue

        print(
            f"[PRICING] BEFORE _search_price query={query!r}",
            flush=True,
        )

        print(
            f"[PRICING_BEFORE_SEARCH] query={query!r}",
            flush=True,
        )

        found = _price_from_market_cache(query, city, item)

        if found.get("status") != "ok":
            print(
                "[MARKET_CACHE_FALLBACK_TO_SEARCH] "
                f"query={query!r} "
                f"category={item.get('category')!r} "
                f"reason={found.get('reason')!r}",
                flush=True,
            )
            found = _search_price(query, city, item)

        print(
            "[PRICING_AFTER_SEARCH] "
            f"query={query!r} "
            f"status={found.get('status')!r} "
            f"reason={found.get('reason')!r}",
            flush=True,
        )

        print(
            f"[PRICING] AFTER _search_price status={found.get('status')!r}",
            flush=True,
        )

        row = dict(item)
        row["pricing_status"] = found.get("status")
        row["pricing_source"] = found.get("source")
        row["pricing_query"] = found.get("query")
        row["pricing_reason"] = found.get("reason")
        row["source_title"] = found.get("source_title")
        row["source_url"] = found.get("source_url")
        row["market_updated_at"] = found.get("market_updated_at")
        row["market_city"] = found.get("market_city")

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