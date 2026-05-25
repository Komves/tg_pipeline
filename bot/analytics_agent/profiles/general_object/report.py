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
    print(f"[general_object][search] query={query!r} status={r.status_code}", flush=True)
    if r.status_code != 200:
        print(f"[general_object][search_error_body] {r.text[:500]}", flush=True)

    r.raise_for_status()

    rows = []
    for x in (r.json().get("web") or {}).get("results") or []:
        rows.append({
            "title": compact(x.get("title") or ""),
            "url": compact(x.get("url") or ""),
            "description": compact(x.get("description") or ""),
        })

    return rows


def _extract_labeled_value(text: str, labels: List[str]) -> str:
    for label in labels:
        m = re.search(
            rf"{label}\s*:\s*([^\n\r]+)",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            value = compact(m.group(1))
            value = re.split(
                r"\s+(Бренд|Модель|Надписи|Материал|Состояние|Назначение|Видимые дефекты)\s*:",
                value,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            return compact(value)
    return ""


def _safe_search_base(object_name: str) -> str:
    text = compact(object_name)
    brand = _extract_labeled_value(text, ["бренд", "brand"])
    model = _extract_labeled_value(text, ["модель", "model"])
    labels = _extract_labeled_value(text, ["надписи", "markings", "labels"])

    parts = []

    if brand:
        parts.append(brand)

    if model:
        parts.append(model)

    if not model and labels:
        cleaned_labels = re.sub(r"[“”\"']", " ", labels)
        cleaned_labels = re.sub(r"[,;/|]+", " ", cleaned_labels)
        parts.append(cleaned_labels)

    if parts:
        base = compact(" ".join(parts))
    else:
        base = text

    base = re.sub(r"[^\w\sА-Яа-яЁё\-\./]", " ", base)
    base = compact(base)

    words = base.split()
    return " ".join(words[:10])


def _build_search_bases(object_name: str) -> List[str]:
    base = _safe_search_base(object_name)

    bases = [base]

    if re.search(r"\btoroline\b", base, flags=re.IGNORECASE):
        bases.append(re.sub(r"\btoroline\b", "Proline", base, flags=re.IGNORECASE))

    if re.search(r"\bproline\b", base, flags=re.IGNORECASE):
        bases.append(re.sub(r"\bproline\b", "Toroline", base, flags=re.IGNORECASE))

    if "Marlin" in base and "Force" in base:
        bases.append("Marlin Force лодочный мотор")

    out = []
    seen = set()
    for x in bases:
        x = compact(x)
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)

    return out


def _build_identity_queries(object_name: str) -> List[str]:
    queries = []

    for base in _build_search_bases(object_name):
        queries.extend([
            f"{base} официальный сайт",
            f"{base} характеристики",
            f"{base} технические характеристики",
            f"{base} specs",
            f"{base} модель серия модификация",
            f"{base} инструкция manual",
            f"{base} каталог",
        ])

    return queries


def _build_review_queries(object_name: str, followup: str = "") -> List[str]:
    queries = []

    for base in _build_search_bases(object_name):
        qbase = compact(f"{base} {followup}")
        queries.extend([
            f"{qbase} отзывы владельцев",
            f"{qbase} обзор",
            f"{qbase} форум",
            f"{qbase} проблемы",
            f"{qbase} ресурс",
            f"{qbase} сравнение",
        ])

    return queries


def _build_price_queries(object_name: str) -> List[str]:
    queries = []

    for base in _build_search_bases(object_name):
        queries.extend([
            f"{base} цена",
            f"{base} купить",
            f"{base} Avito",
            f"{base} Ozon",
            f"{base} Wildberries",
        ])

    return queries

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

    print(f"[general_object][object_name] {object_name!r}", flush=True)
    print(f"[general_object][search_base] {_safe_search_base(object_name)!r}", flush=True)

    identity_queries = _build_identity_queries(object_name)
    review_queries = _build_review_queries(object_name, followup)
    price_queries = _build_price_queries(object_name)

    queries = (
        identity_queries
        + review_queries
        + price_queries
    )

    print(f"[general_object][queries] {json.dumps(queries, ensure_ascii=False)}", flush=True)

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

    print(f"[general_object][results_count] all={len(all_results)} unique={len(unique_results)}", flush=True)
    for idx, item in enumerate(unique_results[:10], start=1):
        print(
            "[general_object][result] "
            f"{idx}. title={item.get('title')!r} url={item.get('url')!r} query={item.get('search_query')!r}",
            flush=True,
        )

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

    review_count = 0
    market_count = 0
    spec_count = 0

    for item in unique_results[:30]:
        source_lines.append(
            "- "
            + compact(item.get("title") or "")
            + "\n  "
            + compact(item.get("description") or "")
            + "\n  "
            + compact(item.get("url") or "")
        )

        txt = (
            (item.get("title") or "")
            + " "
            + (item.get("description") or "")
            + " "
            + (item.get("url") or "")
        ).lower()

        if any(x in txt for x in ["отзыв", "review", "форум"]):
            review_count += 1

        if any(x in txt for x in [
            "характерист",
            "spec",
            "manual",
            "catalog",
            "official",
            "официаль",
        ]):
            spec_count += 1

        if any(x in txt for x in [
            "avito",
            "ozon",
            "wildberries",
            "wb",
            "цена",
            "купить",
        ]):
            market_count += 1

    print(
        f"[general_object][density] review={review_count} market={market_count} spec={spec_count}",
        flush=True,
    )

    client = OpenAI()

    resp = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты аналитик физических объектов: товаров, техники, одежды, моторов, инструментов, снастей, электроники.\n"
                    "КРИТИЧНО:\n"
                    "- не смешивай разные линейки и поколения товара\n"
                    "- не переноси проблемы старых моделей на новые серии без прямых подтверждений\n"
                    "- если отзывов мало — прямо говори об этом\n"
                    "- если модель новая — не выдумывай массовые проблемы\n"
                    "- не делай generic выводы про 'все китайские моторы'\n"
                    "- отделяй подтвержденные проблемы от предположений\n"
                    "- оценивай уверенность выводов\n"
                    "- если данных мало — так и пиши\n"
                    "- сначала попытайся точно определить модель, серию, поколение и модификацию объекта\n"
                    "- перечисли все найденные технические характеристики\n"
                    "- разделяй: подтверждено / вероятно / не удалось подтвердить\n"
                    "- если найдены характеристики только частично — прямо перечисли, чего не хватает\n"
                    "- не отбрасывай слова из названия объекта как 'маркетинг' без подтверждения источниками\n"
                    "- если есть несколько возможных моделей — перечисли их и оцени вероятность\n"
                    "- запрещено писать финальную точную модель без подтверждения источниками\n"
                    "- запрещено приписывать состояние объекта: б/у, новый, грязный, рабочий, неисправный — если это не указано пользователем или не видно из входных данных\n"
                    "- если пользователь прямо уточнил модель/линию, используй это как основной контекст, но всё равно проверяй по web-источникам\n"
                    "- если web-поиск не дал характеристик, не лей воду: напиши, какие данные нужны для точной идентификации\n"
                    "Твоя задача — не болтать, а сделать практический анализ объекта по web-выдаче и данным пользователя.\n"
                    "Не заявляй точную оригинальность, серийники, гарантию или юридическую проверку.\n"
                    "Если данных мало — прямо скажи, что уверенность ограничена.\n"
                    "Формат строго:\n\n"
                    "🔎 Что это\n"
                    "- ...\n\n"
                    "🧾 Характеристики\n"
                    "- бренд\n"
                    "- точная модель\n"
                    "- серия / линейка\n"
                    "- модификация\n"
                    "- назначение\n"
                    "- технические характеристики\n"
                    "- конструктивные особенности\n"
                    "- что подтверждено\n"
                    "- что не удалось подтвердить\n\n"
                    "📌 Где применяется\n"
                    "- ...\n\n"
                    "🌐 Что пишут владельцы\n"
                    "+ ...\n"
                    "- ...\n\n"
                    "⚠️ Типовые проблемы\n"
                    "- ...\n\n"
                    "💰 По рынку\n"
                    "- примерный диапазон цен\n"
                    "- overpriced / норм / выгодно\n"
                    "- если цена не найдена — прямо сказать\n\n"
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
                    f"review_density: {review_count}\n"
                    f"market_density: {market_count}\n"
                    f"spec_density: {spec_count}\n"
                    f"search_base: {_safe_search_base(object_name)}\n\n"
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