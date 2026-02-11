# c_youtube_fetcher.py
from __future__ import annotations

import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Set

from openai import OpenAI

MODEL = os.getenv("C_OPENAI_MODEL", "gpt-4o-mini")

# Сколько ссылок просим у OpenAI за один запрос
CANDIDATES_PER_CALL = int(os.getenv("C_YT_CANDIDATES_PER_CALL", "12"))
# Сколько раз максимум дергаем OpenAI, если не набрали limit живых
MAX_CALLS = int(os.getenv("C_YT_MAX_CALLS", "3"))

URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_\-]{11}")


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[c_youtube] {now} {msg}", flush=True)


def _extract_video_id(url: str) -> str:
    m = re.search(r"v=([A-Za-z0-9_\-]{11})", url)
    return m.group(1) if m else ""


def _is_alive(url: str) -> bool:
    # HEAD иногда режут, поэтому fallback на GET с Range (очень легкий)
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=6) as r:
            return int(getattr(r, "status", 0) or 0) == 200
    except Exception:
        pass

    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Range": "bytes=0-1024", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            code = int(getattr(r, "status", 0) or 0)
            return code in (200, 206)
    except Exception:
        return False


def _openai_pick_urls(prompt: str) -> List[str]:
    client = OpenAI()
    resp = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    text = (resp.output_text or "").strip()
    urls = URL_RE.findall(text)
    # нормализуем на https://www.youtube.com/...
    out = []
    for u in urls:
        if u.startswith("http://"):
            u = "https://" + u[len("http://") :]
        if u.startswith("https://youtube.com/"):
            u = "https://www." + u[len("https://") :]
        out.append(u)
    return out


def get_batch(
    *,
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, str]]:
    """
    Возвращает список items для feed c_youtube:
      {"feed":"c_youtube","item_id":vid,"video_id":vid,"url":url,"title":"","source":"yt:{vid}"}
    Дёшево: максимум MAX_CALLS обращений к OpenAI, по CANDIDATES_PER_CALL ссылок в каждом.
    """

    limit = max(0, int(limit))
    if limit <= 0:
        return []

    out: List[Dict[str, str]] = []
    seen_vid: Set[str] = set()

    # меняем “темы” по попыткам — чтобы не упираться в одни и те же мёртвые результаты
    themes = [
        "rock cover live session 2023 2024",
        "metal cover guitar vocal 2022 2023",
        "post-hardcore cover acoustic rock",
        "drum cover rock metal live",
    ]

    for call_idx in range(MAX_CALLS):
        theme = themes[call_idx % len(themes)]

        prompt = (
            f"Найди {CANDIDATES_PER_CALL} РАЗНЫХ YouTube ссылок формата https://www.youtube.com/watch?v=... "
            f"по теме: {theme}. "
            f"Не shorts. Не плейлисты. Только watch?v=. "
            f"Верни только ссылки, по одной на строку."
        )

        urls = _openai_pick_urls(prompt)
        log(f"call={call_idx+1}/{MAX_CALLS} urls_found={len(urls)} theme='{theme}'")

        for url in urls:
            vid = _extract_video_id(url)
            if not vid:
                continue

            if vid in seen_vid:
                continue
            seen_vid.add(vid)

            if vid in posted_video_ids:
                continue

            if not _is_alive(url):
                log(f"skip dead youtube: {url}")
                continue

            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": url,
                    "title": "",
                    "source": f"yt:{vid}",
                    "ts": int(time.time()),
                }
            )

            if len(out) >= limit:
                log(f"returning={len(out)}")
                return out

        # короткая пауза между попытками (и чтоб web_search не вернул тот же срез)
        time.sleep(0.7)

    log(f"returning={len(out)}")
    return out
