from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

import requests
from openai import OpenAI


BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _search_web(query: str, count: int = 8) -> List[Dict[str, str]]:
    if not BRAVE_SEARCH_API_KEY:
        return []

    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
        },
        params={
            "q": query,
            "count": count,
            "search_lang": "ru",
            "country": "RU",
        },
        timeout=20,
    )
    r.raise_for_status()

    rows: List[Dict[str, str]] = []

    for x in (r.json().get("web") or {}).get("results") or []:
        rows.append({
            "title": _compact(x.get("title") or ""),
            "url": _compact(x.get("url") or ""),
            "description": _compact(x.get("description") or ""),
        })

    return rows


def build_market_queries(data: dict[str, Any]) -> list[str]:
    city = _compact(str(data.get("city") or ""))
    rooms = _compact(str(data.get("rooms") or ""))
    area = data.get("area_m2")

    base = _compact(f"{city} {rooms} квартира")

    area_part = ""
    if area:
        area_part = f"{int(float(area))} м2"

    return [
        _compact(f"{base} {area_part} купить Авито"),
        _compact(f"{base} {area_part} купить Циан"),
        _compact(f"{base} {area_part} купить Домклик"),
        _compact(f"{base} {area_part} Яндекс Недвижимость"),
        _compact(f"{base} {area_part} цена за метр"),
        _compact(f"{city} аренда {rooms} квартира Авито"),
    ]


def estimate_market(data: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    city = _compact(str(data.get("city") or ""))
    price_per_m2 = analysis.get("price_per_m2")

    if not city:
        return {
            "market_found": False,
            "market_comment": "Не вижу город — не могу собрать рыночные аналоги.",
            "sources": [],
        }

    if not BRAVE_SEARCH_API_KEY:
        return {
            "market_found": False,
            "market_comment": "Web-search не подключён: нет BRAVE_SEARCH_API_KEY.",
            "sources": [],
        }

    queries = build_market_queries(data)

    all_results: list[dict[str, str]] = []

    for q in queries:
        try:
            for item in _search_web(q):
                item["search_query"] = q
                all_results.append(item)
        except Exception as e:
            all_results.append({
                "search_query": q,
                "title": "SEARCH_ERROR",
                "url": "",
                "description": f"{type(e).__name__}: {e}",
            })

    unique_results: list[dict[str, str]] = []
    seen = set()

    for item in all_results:
        url = item.get("url") or ""
        key = url or (item.get("title") or "") + (item.get("description") or "")
        if not key or key in seen:
            continue

        seen.add(key)
        unique_results.append(item)

    unique_results = unique_results[:25]

    if not unique_results:
        return {
            "market_found": False,
            "market_comment": "Поисковая выдача пустая — рыночный ориентир не собран.",
            "sources": [],
        }

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return {
            "market_found": False,
            "market_comment": "Поиск выполнен, но LLM-синтез не подключён: нет OPENAI_API_KEY.",
            "sources": unique_results[:5],
        }

    client = OpenAI()

    resp = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты аналитик рынка недвижимости.\n"
                    "Твоя задача — по поисковой выдаче оценить объект относительно рынка.\n"
                    "Не выдумывай точные цены. Давай rough ranges.\n"
                    "Используй только search_results.\n"
                    "Если данных мало — прямо скажи.\n\n"
                    "Верни только JSON:\n"
                    "{"
                    "\"market_found\":true|false,"
                    "\"market_range\":\"...\","
                    "\"market_status\":\"ниже рынка|в рынке|завышено|недостаточно данных\","
                    "\"market_comment\":\"...\","
                    "\"bargain_comment\":\"...\","
                    "\"rent_range\":\"...\","
                    "\"risks\":[\"...\"],"
                    "\"sources\":[{\"title\":\"...\",\"url\":\"...\"}]"
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Объект JSON:\n"
                    f"{json.dumps(data, ensure_ascii=False)}\n\n"
                    f"Анализ JSON:\n"
                    f"{json.dumps(analysis, ensure_ascii=False)}\n\n"
                    f"Цена объекта за м2:\n{price_per_m2}\n\n"
                    f"Поисковые результаты JSON:\n"
                    f"{json.dumps(unique_results, ensure_ascii=False)[:40000]}"
                ),
            },
        ],
    )

    text = _compact(getattr(resp, "output_text", "") or "")
    parsed = _safe_json(text)

    if not parsed:
        return {
            "market_found": False,
            "market_comment": "LLM не вернула структурированный market JSON.",
            "sources": unique_results[:5],
        }

    parsed["sources"] = parsed.get("sources") or unique_results[:5]
    return parsed


def _safe_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()

    if not text:
        return {}

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}

    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}