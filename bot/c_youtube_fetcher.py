from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Dict, List, Set

from openai import OpenAI


MODEL = os.getenv("C_OPENAI_MODEL", "gpt-4o-mini")

MAX_RESULTS = 12

client = OpenAI()


YOUTUBE_URL_RE = re.compile(
    r"(https://www\.youtube\.com/watch\?v=[A-Za-z0-9_\-]{11})"
)


def _extract_video_id(url: str) -> str:
    return url.split("v=")[-1][:11]


def _is_valid_video_id(vid: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{11}", vid))


def _dedupe(urls: List[str]) -> List[str]:
    seen = set()
    out = []

    for u in urls:
        vid = _extract_video_id(u)

        if not _is_valid_video_id(vid):
            continue

        if vid in seen:
            continue

        seen.add(vid)
        out.append(u)

    return out


def _fetch_raw(limit: int) -> List[str]:

    prompt = f"""
Дай список {limit*2} актуальных YouTube видео.

Правила:
— только существующие видео
— только youtube.com/watch?v=
— без Shorts
— без плейлистов
— только прямые ссылки

Формат: JSON массив строк.
"""

    resp = client.responses.create(
        model=MODEL,
        input=prompt,
        temperature=0.7,
    )

    text = resp.output_text

    try:
        data = json.loads(text)
    except:
        return []

    urls = []

    for u in data:
        if isinstance(u, str):
            m = YOUTUBE_URL_RE.search(u)
            if m:
                urls.append(m.group(1))

    return urls


def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, float],
):

    raw = _fetch_raw(limit)

    raw = _dedupe(raw)

    out = []

    now = time.time()

    for url in raw:

        vid = _extract_video_id(url)

        if vid in posted_video_ids:
            continue

        out.append(
            {
                "url": url,
                "video_id": vid,
                "source": f"yt:{vid}",
                "title": "",
                "ts": now,
            }
        )

        if len(out) >= limit:
            break

    return out
