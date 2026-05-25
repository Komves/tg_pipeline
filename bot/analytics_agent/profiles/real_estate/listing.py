from __future__ import annotations

import os
import re
from typing import Any, Dict, List

import requests


BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def enrich_listing_data(data: dict[str, Any]) -> dict[str, Any]:
    raw_text = _compact(str(data.get("raw_text") or ""))

    url = _extract_first_url(raw_text)
    if not url:
        return data

    domain = _extract_domain(url)
    listing_id = _extract_listing_id(url)

    rows = _search_listing(raw_text, listing_id or "", domain)

    page_text = _fetch_listing_page_text(url, listing_id or "")

    combined_text = _compact(
        raw_text
        + " "
        + page_text
        + " "
        + " ".join(
            _compact(
                (row.get("title") or "")
                + " "
                + (row.get("description") or "")
                + " "
                + (row.get("url") or "")
            )
            for row in rows
        )
    )

    print(f"[REAL_ESTATE][ENRICH_TEXT] {combined_text[:1200]}", flush=True)

    if not data.get("price_text"):
        price_text = _extract_price_from_text(combined_text)
        if price_text:
            data["price_text"] = price_text

    if not data.get("area_m2"):
        area = _extract_area_from_text(combined_text)
        if area:
            data["area_m2"] = area

    if not data.get("rooms"):
        rooms = _extract_rooms_from_text(combined_text)
        if rooms:
            data["rooms"] = rooms

    if not data.get("floor") or not data.get("floors_total"):
        floor_pair = _extract_floor_pair_from_text(combined_text)
        if floor_pair:
            data["floor"], data["floors_total"] = floor_pair

    if not data.get("city"):
        city = _extract_city_from_text(combined_text)
        if city:
            data["city"] = city

    return data


def _extract_avito_listing_id(text: str) -> str | None:
    match = re.search(r"_(\d{7,12})(?:\?|$)", text)
    if match:
        return match.group(1)

    return None


def _search_listing(
    raw_text: str,
    listing_id: str,
    domain: str = "",
) -> List[Dict[str, str]]:
    
    if not BRAVE_SEARCH_API_KEY:
        return []

    queries = [raw_text]

    if domain and listing_id:
        queries.append(f"site:{domain} {listing_id}")
    elif listing_id:
        queries.append(listing_id)

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

        if listing_id and listing_id not in url:
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

def _fetch_listing_page_text(raw_url: str, listing_id: str) -> str:
    url_match = re.search(r"https?://[^\s]+", raw_url)
    if not url_match:
        return ""

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
        return ""

    html = r.text or ""

    print(
        f"[REAL_ESTATE][FETCH] status={r.status_code} "
        f"url={url} bytes={len(html)}",
        flush=True,
    )

    if r.status_code == 429:
        print("[REAL_ESTATE][FETCH_BLOCKED] returned 429", flush=True)
        return ""

    if r.status_code >= 400:
        return ""

    if listing_id and listing_id not in html:
        print("[REAL_ESTATE][FETCH_ID_NOT_FOUND]", flush=True)

    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return _compact(text[:20000])

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

def _extract_first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s]+", text)
    if not match:
        return None

    return match.group(0)


def _extract_domain(url: str) -> str:
    match = re.search(r"https?://([^/]+)", url)
    if not match:
        return ""

    return match.group(1).lower()


def _extract_listing_id(url: str) -> str | None:

    patterns = [
        r"_(\d{7,12})(?:\?|$)",   # avito
        r"/flat/(\d+)",           # cian
        r"/offer/(\d+)",          # generic
        r"/card/(\d+)",           # generic
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None

def _extract_area_from_text(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|м\.кв\.?|кв\.?\s*м|метр)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _extract_rooms_from_text(text: str) -> str | None:
    match = re.search(
        r"\b([1-5])\s*[- ]?\s*(?:к|комн|комнат|комнатная)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1)}к"

    return None


def _extract_floor_pair_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"(?:этаж[:\s]*)?(\d{1,2})\s*/\s*(\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1)), int(match.group(2))

    return None


def _extract_city_from_text(text: str) -> str | None:
    lowered = (text or "").lower()

    city_markers = {
        "москва": "Москва",
        "санкт-петербург": "Санкт-Петербург",
        "нижневартовск": "Нижневартовск",
        "новосибирск": "Новосибирск",
        "екатеринбург": "Екатеринбург",
        "тюмень": "Тюмень",
        "сургут": "Сургут",
        "ханты-мансийск": "Ханты-Мансийск",
    }

    for marker, city in city_markers.items():
        if marker in lowered:
            return city

    return None