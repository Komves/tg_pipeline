from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

try:
    from seleniumbase import SB
except Exception as e:
    raise RuntimeError(
        "Не установлен seleniumbase. Выполни: pip install seleniumbase"
    ) from e


SITE = "https://www.vseinstrumenti.ru"

MARKET_QUERIES: list[dict[str, str]] = [
    {
        "key": "putty",
        "title": "Шпаклёвка",
        "query": "шпаклевка финишная",
        "unit": "мешок",
    },
    {
        "key": "primer",
        "title": "Грунтовка",
        "query": "грунтовка глубокого проникновения 10 л",
        "unit": "канистра",
    },
    {
        "key": "rough_mix",
        "title": "Ровнитель",
        "query": "ровнитель для пола сухая смесь",
        "unit": "мешок",
    },
    {
        "key": "flooring",
        "title": "Ламинат",
        "query": "ламинат 33 класс",
        "unit": "м²",
    },
    {
        "key": "bathroom_tile",
        "title": "Плитка",
        "query": "плитка для ванной",
        "unit": "м²",
    },
    {
        "key": "plinth",
        "title": "Плинтус",
        "query": "плинтус напольный",
        "unit": "м",
    },
    {
        "key": "paint_or_wallpaper",
        "title": "Краска",
        "query": "краска интерьерная моющаяся",
        "unit": "ведро",
    },
    {
        "key": "tile_adhesive",
        "title": "Клей плиточный",
        "query": "клей плиточный",
        "unit": "мешок",
    },
    {
        "key": "grout",
        "title": "Затирка",
        "query": "затирка для плитки",
        "unit": "упаковка",
    },
]


def extract_prices_rub(html: str) -> list[int]:
    text = (html or "").replace("\u00a0", " ")
    prices: list[int] = []

    patterns = [
        r"(\d[\d\s]{1,10})\s*₽",
        r"(\d[\d\s]{1,10})\s*руб\.?",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = re.sub(r"\D+", "", match.group(1) or "")
            if not raw:
                continue

            try:
                value = int(raw)
            except ValueError:
                continue

            if 20 <= value <= 500_000:
                prices.append(value)

    return sorted(set(prices))


def normalize_prices(prices: list[int]) -> dict[str, Any]:
    if not prices:
        return {
            "status": "unavailable",
            "prices": [],
            "min_price_rub": None,
            "median_price_rub": None,
            "max_price_rub": None,
            "prices_count": 0,
        }

    return {
        "status": "ok",
        "prices": prices[:30],
        "min_price_rub": min(prices),
        "median_price_rub": int(statistics.median(prices)),
        "max_price_rub": max(prices),
        "prices_count": len(prices),
    }


def build_search_url(query: str) -> str:
    return f"{SITE}/search/?what={quote_plus(query)}"


def collect_one(sb: SB, item: dict[str, str], sleep_seconds: float) -> dict[str, Any]:
    url = build_search_url(item["query"])

    print(f"[COLLECT] {item['key']} | {item['query']} | {url}", flush=True)

    sb.open(url)

    try:
        sb.wait_for_element_present("body", timeout=15)
    except Exception:
        pass

    found_prices = False

    for _ in range(int(max(sleep_seconds, 4))):
        html = sb.get_page_source()

        if "₽" in html or "руб." in html:
            found_prices = True
            break

        sb.sleep(1)

    if not found_prices:
        sb.sleep(3)

    html = sb.get_page_source()
    prices = extract_prices_rub(html)
    price_block = normalize_prices(prices)

    result = {
        "key": item["key"],
        "title": item["title"],
        "query": item["query"],
        "unit": item["unit"],
        "source": "ВсеИнструменты",
        "source_domain": "vseinstrumenti.ru",
        "source_url": url,
        **price_block,
    }

    print(
        f"[RESULT] {item['key']} status={result['status']} "
        f"median={result['median_price_rub']} count={result['prices_count']}",
        flush=True,
    )

    return result


def save_cache(results: list[dict[str, Any]], output_path: Path, city: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema": "vesya_market_cache_v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "source": "ВсеИнструменты",
        "items": {row["key"]: row for row in results},
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[SAVED] {output_path}", flush=True)


def git_push_file(path: Path) -> None:
    commands = [
        ["git", "add", str(path)],
        ["git", "commit", "-m", "Update market price snapshot"],
        ["git", "push"],
    ]

    for cmd in commands:
        print(f"[GIT] {' '.join(cmd)}", flush=True)
        subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="analytics_agent/data/market_cache.json",
        help="Куда сохранить market_cache.json внутри проекта",
    )
    parser.add_argument(
        "--city",
        default="Нижневартовск",
        help="Город для подписи snapshot",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=4.0,
        help="Пауза после открытия страницы",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="После сохранения сделать git add/commit/push",
    )
    args = parser.parse_args()

    output_path = Path(args.output)

    results: list[dict[str, Any]] = []

    with SB(uc=True, test=True, locale_code="ru") as sb:
        for item in MARKET_QUERIES:
            try:
                results.append(collect_one(sb, item, args.sleep))
            except Exception as e:
                print(f"[ERROR] {item['key']} {type(e).__name__}: {e}", flush=True)
                results.append(
                    {
                        "key": item["key"],
                        "title": item["title"],
                        "query": item["query"],
                        "unit": item["unit"],
                        "source": "ВсеИнструменты",
                        "source_domain": "vseinstrumenti.ru",
                        "source_url": build_search_url(item["query"]),
                        "status": "unavailable",
                        "reason": f"{type(e).__name__}: {e}",
                        "prices": [],
                        "min_price_rub": None,
                        "median_price_rub": None,
                        "max_price_rub": None,
                        "prices_count": 0,
                    }
                )

    save_cache(results, output_path, args.city)

    if args.push:
        git_push_file(output_path)


if __name__ == "__main__":
    main()
