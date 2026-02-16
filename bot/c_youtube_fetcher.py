# clean youtube fetcher: metadata only, no format selection
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import yt_dlp


COOKIES_PATH = os.getenv("YT_COOKIES_PATH", "/data/cookies.txt").strip()

MAX_QUERIES = int(os.getenv("YT_MAX_QUERIES", "8"))
SEARCH_PER_QUERY = int(os.getenv("YT_SEARCH_PER_QUERY", "8"))
MAX_CHECK_PER_QUERY = int(os.getenv("YT_MAX_CHECK_PER_QUERY", "6"))

MAX_DURATION_SEC = int(os.getenv("YT_MAX_DURATION_SEC", str(12 * 60)))
MIN_DURATION_SEC = int(os.getenv("YT_MIN_DURATION_SEC", "80"))

BAD_TITLE_RE = re.compile(
    r"(?i)\b("
    r"concert|концерт|full|playlist|album|mix|compilation|"
    r"stream|hour|час|"
    r"karaoke|lyrics"
    r")\b"
)

# Обязательное: cover/кавер
MUST_COVER_RE = re.compile(r"(?i)\b(cover|кавер)\b")

# Бан-скрипты (арабский/тайский/японский/китайский/корейский)
BANNED_SCRIPTS_RE = re.compile(r"[\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")

# Разрешаем только если есть кириллица ИЛИ латиница
HAS_CYR_RE = re.compile(r"[А-Яа-яЁё]")
HAS_LAT_RE = re.compile(r"[A-Za-z]")
EN_HINT_RE = re.compile(r"(?i)\b(the|and|or|to|for|with|from|in|on|of|a|an|this|that|live|official|cover|version|remix)\b")

YT_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {ts} UTC {msg}", flush=True)


@dataclass
class YTItem:
    url: str
    video_id: str
    title: str
    source: str
    ts: int


def _has_cookies() -> bool:
    return bool(COOKIES_PATH) and os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0


def _ydl_opts(flat: bool) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 1,
        # КРИТИЧНО: только метаданные
        "simulate": True,
        "ignore_no_formats_error": True,
        # помогает обходить format errors
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    if flat:
        opts["extract_flat"] = True
        opts["default_search"] = "ytsearch"

    if _has_cookies():
        opts["cookiefile"] = COOKIES_PATH

    return opts


def _build_queries() -> List[str]:
    neg = "-concert -live -full -playlist -album -mix -stream -lyrics -karaoke -shorts -short"

    return [

        # популярные cover-запросы (самые эффективные)
         f"best rock metal covers {neg}",
        f"rock metal cover hit {neg}",
        f"famous song metal cover {neg}",
        f"full band metal cover {neg}",
        f"female vocal metal cover {neg}",
        f"guitar rock metal cover {neg}",
        f"classic rock metal cover {neg}",
        f"legendary hit metal cover {neg}",

        # ai only 1-2 queries (optional)
        f"ai cover rock metal {neg}",
    ]

def _extract_video_id(url: str) -> str:
    m = YT_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _search(query: str, n: int) -> List[dict]:
    q = f"ytsearch{n}:{query}"
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(flat=True)) as ydl:
            data = ydl.extract_info(q, download=False)
            return (data or {}).get("entries") or []
    except Exception as e:
        log(f"search error: {e}")
        return []


def _info(url: str) -> Optional[dict]:
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(flat=False)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log(f"info error: {e}")
        return None


def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
    mode: str = "mix",
) -> List[Dict]:

    if limit <= 0:
        return []

    if _has_cookies():
        log(f"cookies OK: {COOKIES_PATH}")
    else:
        log("cookies missing")

    out: List[Dict] = []
    used = set(posted_video_ids or set())

    queries = _build_queries()[:MAX_QUERIES]

    for query in queries:
        if len(out) >= limit:
            break

        candidates = _search(query, SEARCH_PER_QUERY)
        checked = 0

        for c in candidates:
            if len(out) >= limit:
                break
            if checked >= MAX_CHECK_PER_QUERY:
                break

            vid = (c.get("id") or "").strip()
            url = (c.get("url") or "").strip()
            title = (c.get("title") or "").strip()

            if not vid:
                vid = _extract_video_id(url)
            if not vid:
                continue
            if vid in used:
                continue

            checked += 1

            full = _info(url)
            if not full:
                continue

            duration = full.get("duration")
            if duration:
                if duration < MIN_DURATION_SEC or duration > MAX_DURATION_SEC:
                    continue
            view_count = int(full.get("view_count") or 0)
            like_count = int(full.get("like_count") or 0)

            title = (full.get("title") or title or "").strip()
            uploader = (full.get("uploader") or full.get("channel") or "").strip()
            desc = (full.get("description") or "").strip()

            text_blob = " ".join([title, uploader, desc]).strip()

            # popularity score: views dominate, likes add extra signal
            score = (view_count * 1.0) + (like_count * 30.0)

            # soft anti-AI: not a ban, just a penalty so AI doesn't dominate
            blob_low = text_blob.lower()
            if ("ai cover" in blob_low) or ("a.i. cover" in blob_low) or ("rvc" in blob_low) or ("voice model" in blob_low):
                score *= 0.35

            # 1) баним арабский/тайский/японский/китайский/корейский
            if BANNED_SCRIPTS_RE.search(text_blob):
                continue

            # 2) допускаем только если есть кириллица или латиница
            if not (HAS_CYR_RE.search(text_blob) or HAS_LAT_RE.search(text_blob)):
                continue
            has_cyr = bool(HAS_CYR_RE.search(text_blob))
            has_lat = bool(HAS_LAT_RE.search(text_blob))
            if has_lat and (not has_cyr):
                if not EN_HINT_RE.search(text_blob):
                    continue


            # 3) чёрный список по названию
            if BAD_TITLE_RE.search(title):
                continue

            # 4) обязательно cover/кавер (в title/uploader/desc)
            if not MUST_COVER_RE.search(text_blob):
                continue

            url = full.get("webpage_url") or url

            item = YTItem(
                url=url,
                video_id=vid,
                title=title,
                source=f"yt:{vid}",
                ts=int(time.time()),
            )

            out.append(
                {
                    "feed": "c_youtube",
                    "url": item.url,
                    "video_id": item.video_id,
                    "source": item.source,
                    "title": item.title,
                    "ts": item.ts,
                    "score": float(score),
                    "views": view_count,
                    "likes": like_count,
                }
            )

            used.add(vid)

    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    log(f"returning={len(out)}")
    return out

