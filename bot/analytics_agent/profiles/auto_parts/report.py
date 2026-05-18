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


def _build_vehicle_queries(vin: str) -> List[str]:
    vin = _compact(vin).upper()

    return [
        f"{vin} VIN автомобиль",
        f"{vin} комплектация двигатель",
        f"{vin} модель год выпуска",
    ]


def _vehicle_context_from_params(vin: str, params: Dict[str, Any] | None = None) -> str:
    params = params or {}

    parts = [
        str(params.get("make") or "").strip(),
        str(params.get("model") or "").strip(),
        str(params.get("year") or "").strip(),
        str(params.get("engine") or "").strip(),
        str(params.get("drive_body") or "").strip(),
    ]

    vehicle = _compact(" ".join(x for x in parts if x))

    if vin:
        vehicle = _compact(f"{vehicle} VIN {vin}")

    return vehicle


def _build_part_queries(
    vin: str,
    product: str,
    vehicle_summary: str = "",
    params: Dict[str, Any] | None = None,
) -> List[str]:
    vin = _compact(vin).upper()
    product = _compact(product)
    vehicle = _vehicle_context_from_params(vin, params)

    if not vehicle:
        vehicle = _compact(vehicle_summary)

    base = _compact(f"{vehicle} {product}")

    queries = [
        f"{base} варианты артикулы аналоги",
        f"{base} отзывы владельцев",
        f"{base} форум отзывы проблемы",
        f"{base} drive2 отзывы",
        f"{base} цена купить",
        f"{base} подделка риск",
    ]

    return [q for q in queries if _compact(q)]


def build_vehicle_summary(vin: str) -> str:
    vin = _compact(vin).upper()

    if not vin:
        return "VIN не указан."

    if not BRAVE_SEARCH_API_KEY:
        return (
            f"VIN: {vin}\n"
            "Поиск не подключён: нет BRAVE_SEARCH_API_KEY.\n"
            "Авто нельзя определить без web-search слоя."
        )

    all_results = []

    for q in _build_vehicle_queries(vin):
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

    unique_results = []
    seen = set()

    for item in all_results:
        url = item.get("url") or ""
        key = url or (item.get("title") or "") + (item.get("description") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique_results.append(item)

    unique_results = unique_results[:15]

    try:
        client = OpenAI()

        resp = client.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты автоэксперт. По VIN и поисковым результатам кратко определяешь автомобиль.\n"
                        "Не выдумывай. Если данных мало — пиши, что определение предварительное.\n"
                        "Формат:\n"
                        "VIN\n"
                        "Предварительно определённый автомобиль\n"
                        "Что известно\n"
                        "Что не подтверждено"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"VIN: {vin}\n\n"
                        f"Поисковые результаты JSON:\n"
                        f"{json.dumps(unique_results, ensure_ascii=False)[:30000]}"
                    ),
                },
            ],
        )

        result = (getattr(resp, "output_text", "") or "").strip()
    except Exception as e:
        result = ""

    if not result:
        result = (
            f"VIN: {vin}\n"
            "Авто предварительно определить не удалось. "
            "Можно продолжить подбор, но применимость детали обязательно подтвердить по VIN у продавца."
        )

    return result


def build_auto_parts_report(
    task_id: str,
    vin: str,
    part: str,
    params: Dict[str, Any] | None = None,
) -> str:
    vin = _compact(vin).upper()
    part = _compact(part)
    params = params or {}
    vehicle_summary = _compact(str(params.get("vehicle_summary") or ""))
    product = _compact(str(params.get("product") or part))
    vehicle_context = _vehicle_context_from_params(vin, params)
    query = _compact(f"{vehicle_context} {product}")
    if not vin or not part:
        return (
            "🔧 Анализ автотовара\n\n"
            f"ID: {task_id}\n"
            "Не вижу VIN или название товара/детали. Сценарий должен идти так: VIN → авто → товар."
        )

    if not BRAVE_SEARCH_API_KEY:
        return (
            "🔧 Подбор автозапчастей\n\n"
            f"ID: {task_id}\n"
            "Поиск не подключён: нет BRAVE_SEARCH_API_KEY.\n\n"
            "Для MVP нужен web-search слой, иначе Веся будет гадать."
        )

    all_results = []

    for q in _build_part_queries(vin, product, vehicle_summary, params):
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
                    "Ты автоаналитик качества автотоваров и автодеталей.\n"
                    "Ты НЕ заменяешь каталог применимости Exist/Emex/TecDoc.\n"
                    "Твоя задача — найти кандидатов по авто и товару, затем дать анализ качества по отзывам.\n\n"
                    "Правила:\n"
                    "1. Не утверждай, что товар точно подходит к авто, если нет явного подтверждения.\n"
                    "2. Не выдумывай бренды, артикулы, цены и отзывы. Используй только поисковые результаты.\n"
                    "3. Если источник нерелевантен другой марке/модели/товару — отбрось его и не цитируй как полезный.\n"
                    "4. Главный фокус — качество: реальные плюсы, минусы, частые жалобы, ресурс, надежность, риск подделок.\n"
                    "5. Критерии качества выбирай по типу товара. Для резины, дисков, масла, аккумулятора, колодок критерии разные.\n"
                    "6. Цены давай только если они есть в найденных результатах, иначе пиши, что надежного диапазона нет.\n"
                    "7. Итог должен помогать выбрать, что проверять и какие варианты выглядят лучше/хуже.\n\n"
                    "Формат ответа должен быть КОРОТКИМ.\n"
                    "Без воды, повторов и длинных объяснений.\n"
                    "Не пиши общие фразы.\n"
                    "Не объясняй очевидное.\n\n"

                    "Формат:\n\n"

                    "🔧 <машина> — <товар>\n\n"

                    "Лучшие варианты:\n"
                    "1. бренд / артикул\n"
                    "+ плюсы\n"
                    "- минусы\n\n"

                    "2. бренд / артикул\n"
                    "+ плюсы\n"
                    "- минусы\n\n"

                    "Отзывы:\n"
                    "- краткая суть отзывов\n\n"

                    "Мой вывод:\n"
                    "- лучший баланс цена/качество\n"
                    "- чего избегать\n\n"

                    "Проверить перед заказом:\n"
                    "- только 2-3 реально важных пункта\n\n"

                    "Максимум 1200-1500 символов."
                    "Как понята машина\n"
                    "Найденные кандидаты\n"
                    "Что говорят отзывы\n"
                    "Сильные стороны\n"
                    "Слабые места и жалобы\n"
                    "Риски покупки\n"
                    "Мой вывод\n"
                    "Что проверить перед заказом"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"ID задачи: {task_id}\n"
                    f"VIN:\n{vin}\n\n"
                    f"Контекст авто от пользователя:\n{vehicle_context}\n\n"
                    f"Предварительное определение авто:\n{vehicle_summary}\n\n"
                    f"Товар/деталь для анализа:\n{product}\n\n"
                    f"Поисковые результаты JSON:\n"
                    f"{json.dumps(unique_results, ensure_ascii=False)[:50000]}"
                ),
            },
        ],
    )

    result = (getattr(resp, "output_text", "") or "").strip()

    if not result:
        result = (
            "🔧 Анализ автотовара\n\n"
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