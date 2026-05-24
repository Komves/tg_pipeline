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

    rows = _search_listing(raw_text, listing_id)

    price_text = _extract_price_from_listing_results(rows, listing_id)

    if not price_text:
        price_text = _fetch_price_from_listing_page(raw_text, listing_id)

    if price_text:
        data["price_text"] = price_text

    return data


def _extract_avito_listing_id(text: str) -> str | None:
    match = re.search(r"_(\d{7,12})(?:\?|$)", text)
    if match:
        return match.group(1)

    return None


def _search_listing(
    raw_text: str,
    listing_id: str,
) -> List[Dict[str, str]]:
    if not BRAVE_SEARCH_API_KEY:
        return []

    queries = [
        raw_text,
        f"site:avito.ru {listing_id}",
    ]

    rows: List[Dict[str, str]] = []

    print(f"[REAL_ESTATE][LISTING] listing_id={listing_id}")

    for query in queries:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            },
            params={
                "q": query,
                "count": 10,
                "search_lang": "ru",
                "country": "RU",
            },
            timeout=20,
        )

        r.raise_for_status()
        payload = r.json()

        results = (payload.get("web") or {}).get("results") or []

        print(
            f"[REAL_ESTATE][LISTING] query={query!r} "
            f"results={len(results)}"
        )

        for item in results:

            row = {
                "title": _compact(item.get("title") or ""),
                "url": _compact(item.get("url") or ""),
                "description": _compact(item.get("description") or ""),
            }

            print(
                "[REAL_ESTATE][LISTING][ROW]",
                row["title"],
                row["url"],
                row["description"][:200],
            )

            rows.append(row)
        payload = r.json()

        results = (payload.get("web") or {}).get("results") or []

        print(
            f"[REAL_ESTATE][LISTING] query={query!r} "
            f"results={len(results)}"
        )
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

        print(
            f"[REAL_ESTATE][PRICE_CHECK] "
            f"url={url} "
            f"text={text[:300]}"
        )

        price = _extract_price_from_text(text)
        if price:
            print(
                f"[REAL_ESTATE][PRICE_FOUND] "
                f"listing_id={listing_id} "
                f"price={price}"
            )
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
def _fetch_price_from_listing_page(raw_url: str, listing_id: str) -> str | None:
    url_match = re.search(r"https?://[^\s]+", raw_url)
    if not url_match:
        return None

    url = url_match.group(0)

    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            timeout=20,
        )
    except Exception as e:
        print(f"[REAL_ESTATE][FETCH_ERROR] {type(e).__name__}: {e}", flush=True)
        return None

    print(
        f"[REAL_ESTATE][FETCH] status={r.status_code} "
        f"url={url} bytes={len(r.text or '')}",
        flush=True,
    )

    if r.status_code >= 400:
        return None

    html = r.text or ""

    if listing_id not in html and "avito" not in html.lower():
        return None

    patterns = [
        r'\\?"(?:price|itemPrice|dynx_price)\\?"\s*:\s*\\?"?(?P<price>\d{6,12})\\?"?',
        r'\\?"priceValue\\?"\s*:\s*\\?"?(?P<price>\d{6,12})\\?"?',
        r'\\?"value\\?"\s*:\s*\\?"?(?P<price>\d{6,12})\\?"?\s*,\s*\\?"currency\\?"\s*:\s*\\?"RUB\\?"',
        r'(?P<price>\d[\d\s]{5,})\s*₽',
        r'(?P<price>\d[\d\s]{5,})\s*руб',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue

        digits = re.sub(r"\s+", "", match.group("price"))

        try:
            value = int(digits)
        except ValueError:
            continue

        if value < 300_000 or value > 500_000_000:
            continue

        price = f"{value:,}".replace(",", " ")
        print(
            f"[REAL_ESTATE][FETCH_PRICE_FOUND] listing_id={listing_id} price={price}",
            flush=True,
        )
        return price

    _debug_price_html_fragments(html)
    print("[REAL_ESTATE][FETCH_PRICE_NOT_FOUND]", flush=True)
    return None

def _debug_price_html_fragments(html: str) -> None:
    markers = [
        "price",
        "Price",
        "цена",
        "Цена",
        "₽",
        "руб",
        "10500000",
        "10 500 000",
    ]

    printed = 0
    seen = set()

    for marker in markers:
        start = 0

        while True:
            idx = html.find(marker, start)
            if idx < 0:
                break

            left = max(0, idx - 250)
            right = min(len(html), idx + 500)
            fragment = html[left:right]

            key = fragment[:120]
            if key not in seen:
                seen.add(key)
                print(
                    "[REAL_ESTATE][HTML_FRAGMENT]",
                    marker,
                    fragment.replace("\n", " ")[:900],
                    flush=True,
                )
                printed += 1

            if printed >= 12:
                return

            start = idx + len(marker)