import os
import re
import json
import time
from typing import List, Dict, Any

from openai import OpenAI

MODEL = os.getenv("C_OPENAI_MODEL", "gpt-4o-mini")
DEBUG = True

client = OpenAI()


def _dbg(msg):
    if DEBUG:
        print(f"[c_youtube] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")


YOUTUBE_RE = re.compile(
    r"https://www\.youtube\.com/watch\?v=([a-zA-Z0-9_-]{6,})"
)


def _extract_video_id(url: str) -> str:
    m = YOUTUBE_RE.search(url or "")
    return m.group(1) if m else ""


def _search_youtube_raw() -> str:

    resp = client.responses.create(
        model=MODEL,
        input="Find 8 fresh viral YouTube videos and return ONLY JSON array with urls",
        tools=[{"type": "web_search"}],
    )

    text = getattr(resp, "output_text", "") or ""
    _dbg(f"raw_len={len(text)}")
    return text


def _extract_urls(raw: str) -> List[str]:

    urls = YOUTUBE_RE.findall(raw)

    return list(dict.fromkeys(
        f"https://www.youtube.com/watch?v={vid}" for vid in urls
    ))


def get_batch(
    limit: int,
    posted_video_ids: set,
    last_sent_by_source: dict
) -> List[Dict[str, Any]]:

    raw = _search_youtube_raw()

    urls = _extract_urls(raw)

    _dbg(f"urls_found={len(urls)}")

    out = []

    now = int(time.time())

    for url in urls:

        vid = _extract_video_id(url)

        if not vid:
            continue

        if vid in posted_video_ids:
            continue

        out.append({
            "url": url,
            "video_id": vid,
            "source": f"yt:{vid}",
            "title": "",
            "ts": now,
        })

        if len(out) >= limit:
            break

    _dbg(f"returning={len(out)}")

    return out
