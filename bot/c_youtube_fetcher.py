# clean youtube fetcher: metadata only, no format selection
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp


COOKIES_PATH = os.getenv("YT_COOKIES_PATH", "/data/cookies.txt").strip()

MAX_QUERIES = int(os.getenv("YT_MAX_QUERIES", "8"))
SEARCH_PER_QUERY = int(os.getenv("YT_SEARCH_PER_QUERY", "6"))
MAX_CHECK_PER_QUERY = int(os.getenv("YT_MAX_CHECK_PER_QUERY", "10"))
INFO_WORKERS = int(os.getenv("YT_INFO_WORKERS", "6"))

MAX_DURATION_SEC = int(os.getenv("YT_MAX_DURATION_SEC", str(12 * 60)))
MIN_DURATION_SEC = int(os.getenv("YT_MIN_DURATION_SEC", "80"))
MIN_VIEW_COUNT = int(os.getenv("YT_MIN_VIEWS", "50000"))
FALLBACK_MIN_VIEWS = int(os.getenv("YT_FALLBACK_MIN_VIEWS", "50000"))

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
        "retries": 5,
        "extractor_retries": 5,
        "fragment_retries": 5,
        "sleep_interval_requests": 1.0,   # пауза между HTTP запросами
        "sleep_interval": 1.0,            # базовая пауза
        "max_sleep_interval": 2.0,        # рандомизация паузы до 2с
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        },

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
        f"rock metal covers {neg}",
        f"rock metal cover hit {neg}",
        f"famous song metal cover {neg}",
        f"full band metal cover {neg}",
        f"legendary hit metal cover {neg}",
        f"Русские каверы рок {neg}",
        f"Русские метал каверы {neg}",
        
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
def _info_many(urls: List[str], max_workers: int) -> Dict[str, Optional[dict]]:
    if not urls:
        return {}
    mw = max(1, int(max_workers or 1))
    out: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut_map = {ex.submit(_info, u): u for u in urls}
        for fut in as_completed(fut_map):
            u = fut_map[fut]
            try:
                out[u] = fut.result()
            except Exception:
                out[u] = None
    return out



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

    for qi, query in enumerate(queries, start=1):
        if len(out) >= limit:
            break

        t0 = time.time()
        log(f"query[{qi}/{len(queries)}]: {query}")

        candidates = _search(query, SEARCH_PER_QUERY)
        log(f"query[{qi}] candidates={len(candidates)}")

        # pick up to MAX_CHECK_PER_QUERY unique urls to probe
        probe: List[tuple[str, str, str]] = []  # (vid, url, title)
        for c in candidates:
            if len(probe) >= MAX_CHECK_PER_QUERY:
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
            if not url:
                continue

            probe.append((vid, url, title))

        urls = [u for (_, u, _) in probe]
        infos = _info_many(urls, INFO_WORKERS)

        accepted_in_query = 0

        for (vid, url, title0) in probe:
            if len(out) >= limit:
                break
            if vid in used:
                continue

            full = infos.get(url)
            if not full:
                continue

            duration = full.get("duration")
            if duration:
                if duration < MIN_DURATION_SEC or duration > MAX_DURATION_SEC:
                    continue

            view_count = int(full.get("view_count") or 0)
            like_count = int(full.get("like_count") or 0)

            # hard popularity gate
            if view_count < MIN_VIEW_COUNT:
                continue

            title = (full.get("title") or title0 or "").strip()
            uploader = (full.get("uploader") or full.get("channel") or "").strip()
            desc = (full.get("description") or "").strip()

            text_blob = " ".join([title, uploader, desc]).strip()

            score = (view_count * 1.0) + (like_count * 30.0)

            blob_low = text_blob.lower()
            if ("ai cover" in blob_low) or ("a.i. cover" in blob_low) or ("rvc" in blob_low) or ("voice model" in blob_low):
                score *= 0.35

            if BANNED_SCRIPTS_RE.search(text_blob):
                continue

            if not (HAS_CYR_RE.search(text_blob) or HAS_LAT_RE.search(text_blob)):
                continue
            has_cyr = bool(HAS_CYR_RE.search(text_blob))
            has_lat = bool(HAS_LAT_RE.search(text_blob))
            if has_lat and (not has_cyr):
                if not EN_HINT_RE.search(text_blob):
                    continue

            if BAD_TITLE_RE.search(title):
                continue

            if not MUST_COVER_RE.search(text_blob):
                continue

            url2 = full.get("webpage_url") or url

            out.append(
                {
                    "feed": "c_youtube",
                    "url": url2,
                    "video_id": vid,
                    "source": f"yt:{vid}",
                    "title": title,
                    "ts": int(time.time()),
                    "score": float(score),
                    "views": view_count,
                    "likes": like_count,
                }
            )

            used.add(vid)
            accepted_in_query += 1
            log(f"picked vid={vid} views={view_count} score={int(score)} title={title[:80]}")

        dt = time.time() - t0
        log(f"query[{qi}] accepted={accepted_in_query} out={len(out)}/{limit} dt={dt:.1f}s")
    
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    log(f"returning={len(out)}")
    return out

