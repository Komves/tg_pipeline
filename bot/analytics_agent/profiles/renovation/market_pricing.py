from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from pathlib import Path


def _market_cache_path() -> Path:
    candidates = [
        Path(os.getenv("DATA_DIR", "")) / "analytics_agent" / "data" / "market_cache.json",
        Path(__file__).resolve().parents[2] / "data" / "market_cache.json",
        Path.cwd() / "analytics_agent" / "data" / "market_cache.json",
    ]

    for path in candidates:
        print(
            f"[MARKET_CACHE_CANDIDATE] path={str(path)!r} exists={path.exists()}",
            flush=True,
        )

        if path.exists():
            print(f"[MARKET_CACHE_PATH_FOUND] path={str(path)!r}", flush=True)
            return path

    print("[MARKET_CACHE_PATH_NOT_FOUND]", flush=True)
    return candidates[0]

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

def _price_from_market_cache(query: str, item: Dict[str, Any]) -> Dict[str, Any]:
    cache = _load_market_cache()
    items = cache.get("items") or {}

    category = str(item.get("category") or "").strip()
    cached = items.get(category)

    print(
        "[MARKET_CACHE_ITEM] "
        f"category={category!r} "
        f"type={type(cached).__name__!r} "
        f"value={cached!r}",
        flush=True,
    )

    if not cached:
        return {
            "status": "unavailable",
            "query": query,
            "reason": f"market cache miss for category={category!r}",
        }

    status = str(cached.get("status") or "").strip().lower()

    print(
        "[MARKET_CACHE_STATUS] "
        f"category={category!r} "
        f"status={status!r}",
        flush=True,
    )

    if status != "ok":
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
        "market_schema": cache.get("schema"),
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
            f"[PRICING] BEFORE market_cache query={query!r}",
            flush=True,
        )

        print(
            f"[PRICING_BEFORE_MARKET_CACHE] query={query!r}",
            flush=True,
        )

        found = _price_from_market_cache(query, item)

        if found.get("status") != "ok":
            print(
                "[MARKET_CACHE_NO_LIVE_FALLBACK] "
                f"query={query!r} "
                f"category={item.get('category')!r} "
                f"reason={found.get('reason')!r}",
                flush=True,
            )

        print(
            "[PRICING_AFTER_MARKET_CACHE] "
            f"query={query!r} "
            f"status={found.get('status')!r} "
            f"reason={found.get('reason')!r}",
            flush=True,
        )

        print(
            f"[PRICING] AFTER market_cache status={found.get('status')!r}",
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