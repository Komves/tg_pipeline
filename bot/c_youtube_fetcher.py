# clean youtube fetcher: metadata only, no format selection
from __future__ import annotations

import os
import re
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yt_dlp

COOKIES_PATH = os.getenv("YT_COOKIES_PATH", "/data/cookies.txt").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
POOL_TTL_SEC = int(os.getenv("YT_POOL_TTL_SEC", str(24 * 3600)))  # 24h
POOL_WORK_PATH = DATA_DIR / "yt_pool_work.json"

# when quota is dead, don't hammer API
API_DISABLED_UNTIL_TS = 0.0

MAX_QUERIES = int(os.getenv("YT_MAX_QUERIES", "8"))
SEARCH_PER_QUERY = int(os.getenv("YT_SEARCH_PER_QUERY", "6"))
WORK_TARGET_N = int(os.getenv("YT_WORK_TARGET_N", "30"))  # how many items keep in yt_pool_work per refresh
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
# cover/кавер как сигнал, но не обязательное
COVER_HINT_RE = re.compile(r"(?i)\b(cover|кавер|version)\b")
OFFICIAL_HINT_RE = re.compile(r"(?i)\b(official|music video|vevo|topic|audio)\b")

# Бан-скрипты (арабский/тайский/японский/китайский/корейский)
BANNED_SCRIPTS_RE = re.compile(r"[\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")

# Разрешаем только если есть кириллица ИЛИ латиница
HAS_CYR_RE = re.compile(r"[А-Яа-яЁё]")
HAS_LAT_RE = re.compile(r"[A-Za-z]")

YT_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})")
_ISO_DUR_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")

def _iso8601_to_seconds(s: str | None) -> int | None:
    if not s:
        return None
    m = _ISO_DUR_RE.match(s.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mnt = int(m.group(2) or 0)
    sec = int(m.group(3) or 0)
    return h * 3600 + mnt * 60 + sec

def _build_queries(mode: str = "mix") -> List[str]:
    neg = "-concert -live -full -playlist -album -mix -stream -lyrics -karaoke -shorts -short"

    en = [
        f"rock metal covers {neg}",
        f"rock metal cover hit {neg}",
        f"famous song metal cover {neg}",
        f"full band metal cover {neg}",
        f"legendary hit metal cover {neg}",
    ]

    ru = [
        f"Русские каверы рок {neg}",
        f"Русские метал каверы {neg}",
        f"рок кавер хит {neg}",
        f"метал кавер популярная песня {neg}",
    ]

    ai = [
        f"ai cover rock metal {neg}",
        f"rvc cover rock metal {neg}",
    ]

    m = (mode or "mix").strip().lower()

    if m == "en":
        return en
    if m == "ru":
        return ru
    if m == "ai":
        return ai

    return en + ru + ai


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
    proxy = (os.getenv("YT_PROXY") or "").strip()
    if proxy:
        opts["proxy"] = proxy

    return opts

def _extract_video_id(url: str) -> str:
    m = YT_ID_RE.search(url or "")
    return m.group(1) if m else ""

def _videos_list_meta(video_ids: list[str]) -> dict[str, dict]:
    """
    videos.list is cheap (1 quota unit) and returns real viewCount/likeCount and ISO duration.
    Returns dict: vid -> {views, likes, duration_sec, title, uploader, description}
    """
    key = (os.getenv("YT_API_KEY") or "").strip()
    if not key:
        return {}

    out: dict[str, dict] = {}
    url = "https://www.googleapis.com/youtube/v3/videos"

    # API limit: 50 ids per call
    for i in range(0, len(video_ids), 50):
        chunk = [x for x in video_ids[i:i+50] if x]
        if not chunk:
            continue

        r = requests.get(url, params={
            "part": "statistics,contentDetails,snippet",
            "id": ",".join(chunk),
            "key": key,
        }, timeout=20)

        if r.status_code != 200:
            log(f"YT videos.list error {r.status_code}: {r.text[:200]}")
            continue

        data = r.json() or {}
        for it in (data.get("items") or []):
            vid = (it.get("id") or "").strip()
            if not vid:
                continue
            st = it.get("statistics") or {}
            cd = it.get("contentDetails") or {}
            sn = it.get("snippet") or {}

            views = int(st.get("viewCount") or 0)
            likes = int(st.get("likeCount") or 0)
            duration_sec = _iso8601_to_seconds(cd.get("duration"))

            out[vid] = {
                "views": views,
                "likes": likes,
                "duration_sec": duration_sec,
                "title": (sn.get("title") or "").strip(),
                "uploader": (sn.get("channelTitle") or "").strip(),
                "description": (sn.get("description") or "").strip(),
            }

    return out

def _search(query: str, max_results: int = 40, *, basic_only: bool = False):
    key = os.getenv("YT_API_KEY")
    global API_DISABLED_UNTIL_TS
    now = time.time()
    if now < API_DISABLED_UNTIL_TS:
        return []
    if not key:
        log("YT_API_KEY missing")
        return []
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "order": "viewCount",
        "key": key,
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            log(f"YT API error {r.status_code}: {r.text[:200]}")
            if r.status_code in (403, 429):
                # quota/limit -> stop touching API for a while
                API_DISABLED_UNTIL_TS = time.time() + 6 * 3600
            return []

        data = r.json()

        out = []
        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            title = item["snippet"]["title"]
            url = f"https://www.youtube.com/watch?v={vid}"

            out.append({
                "id": vid,
                "url": url,
                "title": title,
                "uploader": item["snippet"].get("channelTitle", ""),
            })

        log(f"YT API search ok query='{query}' results={len(out)}")
        if basic_only:
            return out

        # --- добираем статистику и фильтруем ---
        vids = [x["id"] for x in out]
        if not vids:
            return []

        url2 = "https://www.googleapis.com/youtube/v3/videos"
        params2 = {
            "part": "contentDetails,statistics,snippet",
            "id": ",".join(vids),
            "key": key,
        }

        r2 = requests.get(url2, params=params2, timeout=20)
        data2 = r2.json()

        meta = {it["id"]: it for it in data2.get("items", [])}
        # --- подтягиваем страну каналов ---
        channel_ids = []
        for it in meta.values():
            cid = (it.get("snippet", {}) or {}).get("channelId")
            if cid:
                channel_ids.append(cid)

        # unique
        channel_ids = list(dict.fromkeys(channel_ids))

        ch_country = {}
        if channel_ids:
            rch = requests.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part": "snippet",
                "id": ",".join(channel_ids[:50]),
                "key": key,
            }, timeout=20)
            jch = rch.json()
            for ch in jch.get("items", []):
                cid = ch.get("id")
                country = (ch.get("snippet", {}) or {}).get("country") or ""
                ch_country[cid] = country

        filtered = []

        for x in out:
            it = meta.get(x["id"])
            if not it:
                continue

            cid = (it.get("snippet", {}) or {}).get("channelId") or ""
            country = ch_country.get(cid, "")

            title = it["snippet"].get("title", "")
            channel = it["snippet"].get("channelTitle", "")
            desc = it["snippet"].get("description", "")

            blob = f"{title} {channel} {desc}".lower()

            if BANNED_SCRIPTS_RE.search(blob):
                continue

            views = int(it.get("statistics", {}).get("viewCount", 0))

            if views < MIN_VIEW_COUNT:
                continue

            filtered.append(x)

        return filtered


    except Exception as e:
        log(f"YT API search error: {e}")
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
    with ThreadPoolExecutor(max_workers=mw) as ex:
        fut_map = {ex.submit(_info, u): u for u in urls}
        for fut in as_completed(fut_map):
            u = fut_map[fut]
            try:
                out[u] = fut.result()
            except Exception:
                out[u] = None
    return out


def _pool_raw_path_for_today() -> Path:
    # UTC day
    d = datetime.now(timezone.utc).strftime("%Y%m%d")
    return DATA_DIR / f"yt_pool_raw_{d}.json"


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _pool_is_fresh(pool: dict) -> bool:
    try:
        ts = float(pool.get("ts") or 0)
        return (time.time() - ts) < POOL_TTL_SEC
    except Exception:
        return False


def _refresh_pool_mix() -> dict:
    queries = _build_queries("mix")[:MAX_QUERIES]

    raw_items: list[dict] = []
    work_items: list[dict] = []
    seen_vid: set[str] = set()

    for qi, q in enumerate(queries, start=1):
        log(f"POOL refresh query[{qi}/{len(queries)}]: {q}")
        cand = _search(q, SEARCH_PER_QUERY, basic_only=True)  # search.list only

        for c in cand:
            raw_items.append(
                {
                    "q": q,
                    "id": c.get("id"),
                    "title": c.get("title"),
                    "uploader": c.get("uploader"),
                    "url": c.get("url"),
                }
            )

        for c in cand:
            vid = (c.get("id") or "").strip()
            url = (c.get("url") or "").strip()
            title = (c.get("title") or "").strip()
            if (not vid) or (vid in seen_vid) or (not url):
                continue
            seen_vid.add(vid)
            work_items.append({"video_id": vid, "url": url, "title": title, "ts": int(time.time())})

    raw_path = _pool_raw_path_for_today()
    _save_json(raw_path, {"ts": int(time.time()), "queries": queries, "items": raw_items})
    log(f"POOL raw saved: {raw_path} items={len(raw_items)}")

    # Enrich candidates with real stats via videos.list (cheap)
    ids = [(x.get("video_id") or "").strip() for x in work_items]
    ids = [x for x in ids if x]
    meta = _videos_list_meta(ids)

    enriched: list[dict] = []
    for x in work_items:
        vid = (x.get("video_id") or "").strip()
        url = (x.get("url") or "").strip()
        if not vid or not url:
            continue

        m = meta.get(vid)
        if not m:
            continue

        title = (m.get("title") or x.get("title") or "").strip()
        uploader = (m.get("uploader") or "").strip()
        desc = (m.get("description") or "").strip()
        views = int(m.get("views") or 0)
        likes = int(m.get("likes") or 0)
        duration_sec = m.get("duration_sec")

        text_blob = " ".join([title, uploader, desc]).strip()

        # STRICT EN/RU ONLY
        if BANNED_SCRIPTS_RE.search(text_blob):
            continue
        if not (HAS_CYR_RE.search(text_blob) or HAS_LAT_RE.search(text_blob)):
            continue
        
        if BAD_TITLE_RE.search(title):
            continue
        
        if views < MIN_VIEW_COUNT:
            continue

        score = (views * 1.0) + (likes * 30.0)
        blob_low = text_blob.lower()
        if ("ai cover" in blob_low) or ("a.i. cover" in blob_low) or ("rvc" in blob_low) or ("voice model" in blob_low):
            score *= 0.35

        enriched.append({
            "video_id": vid,
            "url": url,
            "title": title,
            "uploader": uploader,
            "description": desc,
            "duration_sec": duration_sec,
            "views": views,
            "likes": likes,
            "score": float(score),
            "ts": int(time.time()),
        })

    enriched.sort(key=lambda z: float(z.get("score") or 0.0), reverse=True)
    if WORK_TARGET_N > 0:
        enriched = enriched[:WORK_TARGET_N]

    pool = {"ts": time.time(), "items": enriched}
    _save_json(POOL_WORK_PATH, pool)
    log(f"POOL work saved: {POOL_WORK_PATH} items={len(enriched)} (top via videos.list)")
    return pool

def _consume_from_pool(limit: int, used: set[str]) -> List[Dict]:
    pool = _load_json(POOL_WORK_PATH, {"ts": 0, "items": []})
    items: list[dict] = list(pool.get("items") or [])

    if not items:
        return []

    take_n = min(len(items), max(limit * 12, 40))
    chunk = items[:take_n]
    rest = items[take_n:]
    
    out: List[Dict] = []
    kept_rest: list[dict] = []

    for x in chunk:
        if len(out) >= limit:
            kept_rest.append(x)
            continue

        vid = (x.get("video_id") or "").strip()
        url = (x.get("url") or "").strip()
        title0 = (x.get("title") or "").strip()

        if not vid or not url:
            continue
        if vid in used:
            continue

        full = x  # already enriched in _refresh_pool_mix()

        url2 = url.strip()
        if "/shorts/" in url2:
            continue  # HARD shorts cut
        
        view_count = int(full.get("views") or 0)
        like_count = int(full.get("likes") or 0)
        if view_count < MIN_VIEW_COUNT:
            continue

        title = (full.get("title") or title0 or "").strip()
        uploader = (full.get("uploader") or "").strip()
        desc = (full.get("description") or "").strip()

        text_blob = f"{title} {uploader} {desc}".strip()

        if BANNED_SCRIPTS_RE.search(text_blob):

            continue
        if not (HAS_CYR_RE.search(text_blob) or HAS_LAT_RE.search(text_blob)):
            continue
        
        if BAD_TITLE_RE.search(title):
            continue

        score = (view_count * 1.0) + (like_count * 30.0)
        blob_low = text_blob.lower()
        if ("ai cover" in blob_low) or ("a.i. cover" in blob_low) or ("rvc" in blob_low) or ("voice model" in blob_low):
            score *= 0.35

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
        log(f"picked vid={vid} views={view_count} score={int(score)} title={title[:80]}")

    # If we failed to pick anything, DO NOT burn the pool.
    # Keep original items so we can debug/try again.
    # If we failed to pick anything, DO NOT burn the pool.
    # Keep original items so we can debug/try again.
    if not out:
        log("picked=0 -> keep pool unchanged (no burn)")
        _save_json(POOL_WORK_PATH, pool)
        return out

    new_pool = {"ts": pool.get("ts") or time.time(), "items": kept_rest + rest}
    _save_json(POOL_WORK_PATH, new_pool)
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

    # B-strategy: one MIX pool
    pool = _load_json(POOL_WORK_PATH, {"ts": 0, "items": []})
    if _pool_is_fresh(pool) and (pool.get("items") or []):
        got = _consume_from_pool(limit, used)
        got.sort(key=lambda x: x.get("score", 0), reverse=True)
        log(f"returning={len(got)} (from pool)")
        return got

    # refresh pool once (expensive but rare)
    if os.getenv("YT_API_KEY"):
        _refresh_pool_mix()

    got = _consume_from_pool(limit, used)
    got.sort(key=lambda x: x.get("score", 0), reverse=True)
    log(f"returning={len(got)} (after refresh)")
    return got


