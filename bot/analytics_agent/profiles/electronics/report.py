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


def _safe_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {}

    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


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


def clarify_electronics_requirements(
    product: str,
    known_details: Dict[str, Any] | None = None,
    user_answer: str = "",
) -> Dict[str, Any]:
    product = _compact(product).lower()
    known_details = known_details or {}

    if not product:
        return {
            "ready": False,
            "category": "",
            "field": "product",
            "next_question": "Напиши, какую электронику анализируем.",
            "known_details": known_details,
            "normalized_product": product,
        }

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        if not known_details:
            return {
                "ready": False,
                "category": product,
                "field": "purpose",
                "next_question": "Для чего нужен товар и какой примерный бюджет?",
                "known_details": known_details,
                "normalized_product": product,
            }

        return {
            "ready": True,
            "category": product,
            "field": "",
            "next_question": "",
            "known_details": known_details,
            "normalized_product": product,
        }

    try:
        client = OpenAI()

        resp = client.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты уточняющий роутер для аналитика качества электроники по отзывам.\n"
                        "Ты НЕ пишешь финальный отчет.\n"
                        "Ты задаешь только ОДИН самый важный следующий вопрос.\n\n"
                        "Категории: ноутбук, смартфон, телевизор, наушники, роутер, монитор, планшет, другое.\n\n"
                        "Правила:\n"
                        "1. Не задавай анкету.\n"
                        "2. Каждый раз спрашивай только один самый важный параметр.\n"
                        "3. Для ноутбука сначала выясни сценарий: офис, игры, монтаж, нейросети, учеба.\n"
                        "4. Для смартфона — что важнее: камера, батарея или производительность.\n"
                        "5. Для телевизора — диагональ.\n"
                        "6. Для наушников — формат: TWS, полноразмерные, проводные.\n"
                        "7. Для роутера — площадь и стены.\n"
                        "8. Для монитора — диагональ/разрешение/игры или работа.\n"
                        "9. Если уже есть назначение и бюджет/размер, можно считать готовым.\n\n"
                        "Верни только JSON:\n"
                        "{"
                        "\"ready\":true|false,"
                        "\"category\":\"...\","
                        "\"field\":\"purpose|budget|size|format|priority|area|resolution|other|\","
                        "\"next_question\":\"...\","
                        "\"known_details\":{...},"
                        "\"normalized_product\":\"...\""
                        "}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"product:\n{product}\n\n"
                        f"known_details:\n{json.dumps(known_details, ensure_ascii=False)}\n\n"
                        f"user_answer:\n{user_answer}"
                    ),
                },
            ],
        )

        data = _safe_json(resp.output_text)

        if isinstance(data, dict) and "ready" in data:
            data["known_details"] = data.get("known_details") or known_details
            data["normalized_product"] = data.get("normalized_product") or product
            return data

    except Exception:
        pass

    if not known_details:
        return {
            "ready": False,
            "category": product,
            "field": "purpose",
            "next_question": "Для чего нужен товар и какой примерный бюджет?",
            "known_details": known_details,
            "normalized_product": product,
        }

    return {
        "ready": True,
        "category": product,
        "field": "",
        "next_question": "",
        "known_details": known_details,
        "normalized_product": product,
    }


def plan_electronics_research(
    product: str,
    product_details: Dict[str, Any] | None = None,
    followup: str = "",
) -> Dict[str, Any]:
    product = _compact(product)
    product_details = product_details or {}
    followup = _compact(followup)

    if not product:
        return {
            "strategy": "spec_required_first",
            "reason": "product is empty",
            "search_queries": [],
            "candidate_focus": "",
        }

    detail_text = " ".join(
        _compact(str(v))
        for v in product_details.values()
        if _compact(str(v))
    )

    if followup:
        detail_text = _compact(f"{detail_text} уточнение: {followup}")

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return {
            "strategy": "reviews_quality",
            "reason": "no llm key",
            "search_queries": [
                f"{product} {detail_text} лучшие модели отзывы недостатки",
                f"{product} {detail_text} форум отзывы проблемы",
                f"{product} {detail_text} рейтинг надежности отзывы",
            ],
            "candidate_focus": product,
        }

    try:
        client = OpenAI()

        resp = client.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты planning-router для аналитика качества электроники по отзывам.\n"
                        "Ты НЕ пишешь финальный отчет.\n"
                        "Ты выбираешь стратегию web-research.\n\n"
                        "Стратегии:\n"
                        "1. model_discovery_then_reviews — сначала найти конкретные модели, потом отзывы.\n"
                        "2. reviews_quality — товар и параметры достаточны, ищем отзывы/проблемы/надежность.\n"
                        "3. spec_required_first — не хватает обязательного параметра.\n"
                        "4. budget_comparison — пользователь просит диапазон бюджета или дешевле/дороже.\n\n"
                        "Верни только JSON:\n"
                        "{"
                        "\"strategy\":\"model_discovery_then_reviews|reviews_quality|spec_required_first|budget_comparison\","
                        "\"reason\":\"...\","
                        "\"search_queries\":[\"...\"],"
                        "\"candidate_focus\":\"...\""
                        "}\n\n"
                        "Правила:\n"
                        "- Запросы должны искать конкретные модели и отзывы.\n"
                        "- Не делай общий поиск 'купить электронику'.\n"
                        "- Добавляй слова: отзывы, недостатки, проблемы, форум, рейтинг, лучшие модели.\n"
                        "- Если пользователь просит дешевле/до суммы/российские — строй запросы под уточнение."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"product:\n{product}\n\n"
                        f"product_details:\n{json.dumps(product_details, ensure_ascii=False)}\n\n"
                        f"followup:\n{followup}"
                    ),
                },
            ],
        )

        data = _safe_json(resp.output_text)

        if isinstance(data, dict) and data.get("search_queries"):
            return data

    except Exception:
        pass

    return {
        "strategy": "reviews_quality",
        "reason": "fallback",
        "search_queries": [
            f"{product} {detail_text} лучшие модели отзывы недостатки",
            f"{product} {detail_text} проблемы отзывы форум",
            f"{product} {detail_text} рейтинг надежности",
        ],
        "candidate_focus": product,
    }


def build_electronics_report(
    task_id: str,
    product: str,
    params: Dict[str, Any] | None = None,
) -> str:
    product = _compact(product)
    params = params or {}

    if not product:
        return (
            "🔌 Анализ электроники\n\n"
            f"ID: {task_id}\n"
            "Не вижу, какую электронику анализируем."
        )

    if not BRAVE_SEARCH_API_KEY:
        return (
            "🔌 Анализ электроники\n\n"
            f"ID: {task_id}\n"
            "Поиск не подключён: нет BRAVE_SEARCH_API_KEY.\n\n"
            "Для этого профиля нужен web-search слой, иначе Веся будет гадать."
        )

    product_details = params.get("product_details") or {}
    followup = _compact(str(params.get("followup") or ""))

    research_plan = plan_electronics_research(
        product,
        product_details,
        followup,
    )

    search_queries = research_plan.get("search_queries") or []

    all_results = []

    for q in search_queries:
        try:
            for item in _search_web(q):
                item["search_query"] = q
                item["research_strategy"] = research_plan.get("strategy") or ""
                all_results.append(item)
        except Exception as e:
            all_results.append({
                "search_query": q,
                "research_strategy": research_plan.get("strategy") or "",
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

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        lines = [
            f"🔌 {product} — анализ по выдаче",
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

    client = OpenAI()

    resp = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты аналитик качества электроники по отзывам.\n"
                    "Ты НЕ каталог товаров и НЕ рекламный обзорщик.\n"
                    "Твоя задача — по поисковой выдаче выделить реальные модели, плюсы, минусы, жалобы и короткий вывод.\n\n"
                    "Жесткие правила:\n"
                    "1. Не выдумывай модели, цены, отзывы и характеристики.\n"
                    "2. Используй только то, что есть в search_results.\n"
                    "3. Не пиши обзор-статью.\n"
                    "4. Если пользователь просит порядок цен — давай диапазоны, не точные цены.\n"
                    "5. Если follow-up задан, отвечай именно на уточнение, не начинай отчет заново.\n"
                    "6. Главный фокус — качество: надежность, частые жалобы, перегрев, батарея, экран, звук, брак, сервис, софт.\n"
                    "7. Если данных мало — прямо скажи, что выдача слабая.\n\n"
                    "Формат:\n"
                    "🔌 <категория> — <условия>\n\n"
                    "Лучшие варианты:\n"
                    "1. <модель>\n"
                    "+ плюсы\n"
                    "- минусы\n\n"
                    "2. <модель>\n"
                    "+ плюсы\n"
                    "- минусы\n\n"
                    "Отзывы:\n"
                    "- краткая суть\n\n"
                    "Мой вывод:\n"
                    "- лучший баланс\n"
                    "- чего избегать\n\n"
                    "Проверить перед покупкой:\n"
                    "- 2–3 пункта"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"task_id:\n{task_id}\n\n"
                    f"product:\n{product}\n\n"
                    f"product_details:\n{json.dumps(product_details, ensure_ascii=False)}\n\n"
                    f"followup:\n{followup}\n\n"
                    f"research_plan:\n{json.dumps(research_plan, ensure_ascii=False)}\n\n"
                    f"search_results:\n{json.dumps(unique_results, ensure_ascii=False)[:12000]}"
                ),
            },
        ],
    )

    return _compact(resp.output_text)