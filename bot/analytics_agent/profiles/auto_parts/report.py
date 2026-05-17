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

    rows = []
    for x in (r.json().get("web") or {}).get("results") or []:
        rows.append({
            "title": _compact(x.get("title") or ""),
            "url": _compact(x.get("url") or ""),
            "description": _compact(x.get("description") or ""),
        })

    return rows


def _build_queries(user_text: str) -> List[str]:
    src = _compact(user_text)

    return [
        f"{src} артикул OEM применимость",
        f"{src} цена купить",
        f"{src} отзывы форум",
        f"{src} плохой хороший аналог отзывы",
        f"{src} подделка риск",
    ]


def build_auto_parts_report(task_id: str, user_text: str, params: Dict[str, Any] | None = None) -> str:
    query = _compact(user_text)

    if not query:
        return (
            "🔧 Подбор автозапчастей\n\n"
            f"ID: {task_id}\n"
            "Не вижу запроса. Напиши VIN/авто и какую деталь ищем."
        )

    if not BRAVE_SEARCH_API_KEY:
        return (
            "🔧 Подбор автозапчастей\n\n"
            f"ID: {task_id}\n"
            "Поиск не подключён: нет BRAVE_SEARCH_API_KEY.\n\n"
            "Для MVP нужен web-search слой, иначе Веся будет гадать."
        )

    all_results = []

    for q in _build_queries(query):
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

    seen = set()
    unique_results = []

    for item in all_results:
        url = item.get("url") or ""
        key = url or (item.get("title") or "") + (item.get("description") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique_results.append(item)

    unique_results = unique_results[:25]

    client = OpenAI()

    resp = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты автоэксперт-подборщик запчастей.\n"
                    "Твоя задача — не угадать деталь, а дать осторожный агрегированный анализ.\n\n"
                    "Правила:\n"
                    "1. Не утверждай применимость детали как факт, если нет явного подтверждения.\n"
                    "2. Цены давай диапазоном, а не одной точной ценой.\n"
                    "3. Отзывы агрегируй: положительные сигналы, жалобы, риск подделок.\n"
                    "4. Разделяй: OEM, аналоги, б/у, сомнительные варианты.\n"
                    "5. Обязательно напиши, что перед заказом нужно подтвердить применимость по VIN у продавца.\n"
                    "6. Если данных мало — прямо скажи, что вывод предварительный.\n\n"
                    "Формат ответа:\n"
                    "🔧 Подбор запчасти\n"
                    "Авто/запрос\n"
                    "Что удалось понять\n"
                    "Кандидаты\n"
                    "Диапазон цен\n"
                    "Анализ отзывов\n"
                    "Риски\n"
                    "Рекомендация\n"
                    "Что проверить перед заказом"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"ID задачи: {task_id}\n"
                    f"Запрос пользователя:\n{query}\n\n"
                    f"Поисковые результаты JSON:\n"
                    f"{json.dumps(unique_results, ensure_ascii=False)[:50000]}"
                ),
            },
        ],
    )

    result = (getattr(resp, "output_text", "") or "").strip()

    if not result:
        result = (
            "🔧 Подбор запчасти\n\n"
            "Не удалось собрать нормальный анализ. Поисковые результаты пустые или модель не вернула ответ."
        )

    sources = []
    for item in unique_results[:8]:
        url = item.get("url")
        title = item.get("title")
        if url:
            sources.append(f"- {title}: {url}" if title else f"- {url}")

    if sources:
        result += "\n\nИсточники для проверки:\n" + "\n".join(sources)

    return result