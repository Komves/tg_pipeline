from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

import requests
from openai import OpenAI

from .parser import compact


BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")


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

    rows = []
    for x in (r.json().get("web") or {}).get("results") or []:
        rows.append({
            "title": compact(x.get("title") or ""),
            "url": compact(x.get("url") or ""),
            "description": compact(x.get("description") or ""),
        })

    return rows


def _build_queries(object_name: str, followup: str = "") -> List[str]:
    base = compact(f"{object_name} {followup}")

    return [
        f"{base} что это модель бренд отзывы",
        f"{base} отзывы владельцев недостатки проблемы форум",
        f"{base} качество стоит ли покупать аналоги",
        f"{base} обзор YouTube отзывы",
        f"{base} цена купить Ozon Wildberries Avito",
    ]


def build_general_object_report(
    task_id: str,
    current_object: Dict[str, Any],
    followup: str = "",
) -> str:
    object_name = compact(
        current_object.get("display_name")
        or current_object.get("object_text")
        or current_object.get("raw_text")
        or ""
    )

    if not object_name:
        return (
            "🔎 Анализ объекта\n\n"
            f"ID: {task_id}\n\n"
            "Не вижу объект анализа. Пришли фото, ссылку, скрин, название или текст объекта."
        )

    if not BRAVE_SEARCH_API_KEY:
        return (
            "🔎 Анализ объекта\n\n"
            f"ID: {task_id}\n\n"
            "Поиск не подключён: нет BRAVE_SEARCH_API_KEY.\n"
            "Для профиля анализа объекта нужен web-search, иначе Веся будет гадать."
        )

    queries = _build_queries(object_name, followup)
    all_results: List[Dict[str, str]] = []

    for q in queries:
        try:
            for item in _search_web(q):
                item["search_query"] = q
                all_results.append(item)
        except Exception as e:
            all_results.append({
                "title": "SEARCH_ERROR",
                "url": "",
                "description": f"{type(e).__name__}: {e}",
                "search_query": q,
            })

    seen = set()
    unique_results = []

    for item in all_results:
        key = item.get("url") or (item.get("title") or "") + (item.get("description") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique_results.append(item)

    unique_results = unique_results[:35]

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        lines = [
            f"🔎 {object_name} — анализ объекта",
            "",
            f"ID: {task_id}",
            "",
            "Поиск выполнен, но LLM-слой не подключён: нет OPENAI_API_KEY.",
            "",
            "Найденные источники:",
        ]

        for item in unique_results[:8]:
            lines.append(f"- {item.get('title') or 'Без названия'}")

        return "\n".join(lines)

    source_lines = []
    for item in unique_results[:12]:
        source_lines.append(
            "- "
            + compact(item.get("title") or "")
            + "\n  "
            + compact(item.get("description") or "")
            + "\n  "
            + compact(item.get("url") or "")
        )

    client = OpenAI()

    resp = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты аналитик физических объектов: товаров, техники, одежды, моторов, инструментов, снастей, электроники.\n"
                    "Твоя задача — не болтать, а сделать практический анализ объекта по web-выдаче и данным пользователя.\n"
                    "Не заявляй точную оригинальность, серийники, гарантию или юридическую проверку.\n"
                    "Если данных мало — прямо скажи, что уверенность ограничена.\n"
                    "Формат строго:\n\n"
                    "🔎 Что это\n"
                    "- ...\n\n"
                    "📌 Где применяется\n"
                    "- ...\n\n"
                    "🌐 Что пишут владельцы\n"
                    "+ ...\n"
                    "- ...\n\n"
                    "⚠️ Типовые проблемы\n"
                    "- ...\n\n"
                    "💰 По рынку\n"
                    "- overpriced / норм / выгодно\n\n"
                    "🔁 Аналоги\n"
                    "- ...\n\n"
                    "🧠 Вердикт Веси\n"
                    "- брать можно / спорно / лучше искать альтернативу"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"task_id: {task_id}\n"
                    f"object:\n{json.dumps(current_object, ensure_ascii=False)}\n\n"
                    f"followup:\n{followup}\n\n"
                    f"web_sources:\n" + "\n\n".join(source_lines)
                ),
            },
        ],
    )

    text = compact(getattr(resp, "output_text", "") or "")

    if not text:
        return (
            f"🔎 {object_name} — анализ объекта\n\n"
            f"ID: {task_id}\n\n"
            "Не удалось собрать вывод по источникам."
        )

    return text