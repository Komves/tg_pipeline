from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any

try:
    from seleniumbase import SB
except Exception as e:
    raise RuntimeError(
        "Не установлен seleniumbase. Выполни: pip install seleniumbase"
    ) from e

PROFI_HOME_URL = "https://profi.ru"

PROFILE_DIR = Path("labor_browser_profile").resolve()
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

PRICE_LIMITS: dict[str, tuple[int, int]] = {
    "laminate_install": (150, 2500),
    "tile_install": (500, 7000),
    "floor_leveling": (150, 5000),
    "wall_putty": (100, 2500),
    "wall_paint": (100, 2000),
    "wall_primer": (30, 700),
    "baseboard_install": (50, 1500),
}


LABOR_SOURCES: list[dict[str, str]] = [
    {
        "key": "laminate_install",
        "title": "Укладка ламината",
        "unit": "м²",
        "url": "https://profi.ru/remont/pol/ukladka-laminata/price/",
    },
    {
        "key": "tile_install",
        "title": "Укладка плитки",
        "unit": "м²",
        "url": "https://profi.ru/remont/plitochniki/price/",
    },
    {
        "key": "floor_leveling",
        "title": "Выравнивание пола",
        "unit": "м²",
        "url": "https://profi.ru/remont/styazhka-pola/price/",
    },
    {
        "key": "wall_putty",
        "title": "Шпаклевка стен",
        "unit": "м²",
        "url": "https://profi.ru/remont/shpaklevka-sten/price/",
    },
    {
        "key": "wall_paint",
        "title": "Покраска стен",
        "unit": "м²",
        "url": "https://profi.ru/remont/pokraska-sten/price/",
    },
    {
        "key": "wall_primer",
        "title": "Грунтовка стен",
        "unit": "м²",
        "url": "https://profi.ru/remont/gruntovka-sten/price/",
    },
    {
        "key": "baseboard_install",
        "title": "Монтаж плинтуса",
        "unit": "м.п.",
        "url": "https://profi.ru/remont/montazh-plintusa/price/",
    },
]


def extract_labor_prices_rub(html: str) -> list[int]:
    text = (html or "").replace("\u00a0", " ")
    prices: list[int] = []

    patterns = [
        r"(?:от|средняя цена|стоимость|цена)\s*(\d[\d\s]{1,10})\s*(?:₽|руб)",
        r"(\d[\d\s]{1,10})\s*(?:₽|руб)\s*/?\s*(?:м²|м2|кв\.?\s*м|пог\.?\s*м|м\.п\.|точк)",
        r"(\d[\d\s]{1,10})\s*(?:₽|руб)",
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

            if 50 <= value <= 50_000:
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

    trimmed = prices[:50]

    return {
        "status": "ok",
        "prices": trimmed,
        "min_price_rub": min(trimmed),
        "median_price_rub": int(statistics.median(trimmed)),
        "max_price_rub": max(trimmed),
        "prices_count": len(trimmed),
    }


def collect_one(sb: SB, item: dict[str, str], sleep_seconds: float) -> dict[str, Any]:
    url = item["url"]

    print(f"[COLLECT] {item['key']} | {item['title']} | {url}", flush=True)

    sb.open(url)

    try:
        sb.wait_for_element_present("body", timeout=20)
    except Exception:
        pass

    sb.sleep(sleep_seconds)

    html = sb.get_page_source()

    debug_dir = Path("labor_debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    debug_html_path = debug_dir / f"{item['key']}.html"
    debug_png_path = debug_dir / f"{item['key']}.png"

    debug_html_path.write_text(html, encoding="utf-8")

    try:
        sb.save_screenshot(str(debug_png_path))
    except Exception:
        pass

    print(
        "[DEBUG_PAGE] "
        f"key={item['key']} "
        f"url={sb.get_current_url()!r} "
        f"html_len={len(html)} "
        f"has_ruble={'₽' in html} "
        f"has_rub={'руб' in html.lower()} "
        f"has_captcha={'captcha' in html.lower()} "
        f"html={debug_html_path} "
        f"png={debug_png_path}",
        flush=True,
    )

    raw_prices = extract_labor_prices_rub(html)

    price_min, price_max = PRICE_LIMITS.get(item["key"], (50, 50_000))
    prices = [
        price
        for price in raw_prices
        if price_min <= price <= price_max
    ]

    print(
        "[PRICE_FILTER] "
        f"key={item['key']} "
        f"raw={raw_prices} "
        f"limits=({price_min}, {price_max}) "
        f"filtered={prices}",
        flush=True,
    )

    price_block = normalize_prices(prices)

    result = {
        "key": item["key"],
        "title": item["title"],
        "title_ru": item["title"],
        "unit": item["unit"],
        "source": "Profi.ru",
        "source_domain": "profi.ru",
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
        "schema": "vesya_labor_cache_v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "source": "Profi.ru",
        "source_policy": "windows_seleniumbase_snapshot",
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
        ["git", "commit", "-m", "Update labor price snapshot"],
        ["git", "push"],
    ]

    for cmd in commands:
        print(f"[GIT] {' '.join(cmd)}", flush=True)
        subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="analytics_agent/data/labor_cache.json",
        help="Куда сохранить labor_cache.json внутри проекта",
    )
    parser.add_argument(
        "--city",
        default="Нижневартовск",
        help="Город для подписи snapshot",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=6.0,
        help="Пауза после открытия страницы услуги",
    )
    parser.add_argument(
        "--login-wait",
        action="store_true",
        help="Открыть Profi.ru и дождаться ручного логина перед сбором",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="После сохранения сделать git add/commit/push",
    )
    args = parser.parse_args()

    output_path = Path(args.output)

    results: list[dict[str, Any]] = []

    with SB(
        uc=True,
        test=True,
        locale_code="ru",
        user_data_dir=str(PROFILE_DIR),
    ) as sb:
        if args.login_wait:
            print("[LOGIN] Открою Profi.ru. Залогинься руками, потом вернись в консоль и нажми Enter.", flush=True)
            sb.open(PROFI_HOME_URL)
            input("[LOGIN] После ручного входа нажми Enter здесь... ")

        for item in LABOR_SOURCES:
            try:
                results.append(collect_one(sb, item, args.sleep))
            except Exception as e:
                print(f"[ERROR] {item['key']} {type(e).__name__}: {e}", flush=True)
                results.append(
                    {
                        "key": item["key"],
                        "title": item["title"],
                        "title_ru": item["title"],
                        "unit": item["unit"],
                        "source": "Profi.ru",
                        "source_domain": "profi.ru",
                        "source_url": item["url"],
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