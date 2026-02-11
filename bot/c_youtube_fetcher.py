from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# =========================
# CONFIG
# =========================
# file with sources (one per line). examples:
# https://www.youtube.com/@somechannel
# https://www.youtube.com/channel/UCxxxx
# https://www.youtube.com/c/SomeName
# https://www.youtube.com/user/SomeName
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
SOURCES_FILE = os.getenv("C_YT_SOURCES_FILE") or os.path.join(REPO_ROOT, "tg_pipeline", "c_youtube_sources.txt")

# how many raw urls we try to parse before filtering/deduping (to avoid infinite loops)
MAX_RAW_URLS = int(os.getenv("C_YT_MAX_RAW_URLS", "80"))

# cooldown per source (seconds): avoid spamming same channel too often
SOURCE_COOLDOWN_SEC = int(os.getenv("C_YT_SOURCE_COOLDOWN_SEC", str(60 * 60 * 6)))  # 6h

# request timeout
HTTP_TIMEOUT = float(os.getenv("C_YT_HTTP_TIMEOUT_SEC", "20"))

# user agent
UA = os.getenv("C_YT_UA", "Mozilla/5.0 (compatible; tg_pipeline/1.0)")

# enable debug prints
DEBUG = (os.getenv("C_YT_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}


def _log(msg: str) -> None:
    if DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# YT URL helpers
# =========================
_YT_WATCH_RE = re.compile(r"(?:youtube\.com/watch\?[^#]*v=|youtu\.be/)([A-Za-z0-9_-]{6,})", re.IGNORECASE)
_YT_SHORTS_RE = re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", re.IGNORECASE)

# very cheap garbage filter (keep only sane urls)
_BAD_URL_RE = re.compile(r"\s")  # spaces are not allowed in urls


def _extract_video_id(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    m = _YT_WATCH_RE.search(u)
    if m:
        return m.group(1)
    m = _YT_SHORTS_RE.search(u)
    if m:
        return m.group(1)
    return ""


def _norm_watch_url(url: str) -> str:
    vid = _extract_video_id(url)
    if not vid:
        return ""
    return f"https://www.youtube.com/watch?v={vid}"


# =========================
# Live check (oEmbed)
# =========================
def _yt_is_alive(url: str) -> bool:
    """
    oEmbed is a lightweight way to check if YouTube recognizes the URL.
    It can be flaky sometimes; but for "video is unavailable" it works well.
    """
    u = _norm_watch_url(url)
    if not u:
        return False

    oembed = "https://www.youtube.com/oembed"
    try:
        r = requests.get(
            oembed,
            params={"url": u, "format": "json"},
            headers={"User-Agent": UA},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as e:
        _log(f"oembed exception: {e}")
        # network hiccup -> treat as alive to avoid dropping everything
        return True

    if r.status_code == 200:
        return True

    # 401/403/404 typical when not embeddable / deleted / private
    if r.status_code in (401, 403, 404):
        return False

    # other codes: be permissive
    return True


def _yt_title_oembed(url: str) -> str:
    u = _norm_watch_url(url)
    if not u:
        return ""
    oembed = "https://www.youtube.com/oembed"
    try:
        r = requests.get(
            oembed,
            params={"url": u, "format": "json"},
            headers={"User-Agent": UA},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        t = (data.get("title") or "").strip()
        return t
    except Exception:
        return ""


# =========================
# Source discovery
# =========================
def _load_sources() -> List[str]:
    if not os.path.exists(SOURCES_FILE):
        _log(f"sources file not found: {SOURCES_FILE}")
        return []
    out: List[str] = []
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            t = (line or "").strip()
            if not t or t.startswith("#"):
                continue
            out.append(t)
    return out


def _source_key(src: str) -> str:
    return (src or "").strip().lower().rstrip("/")


def _cooldown_ok(src: str, last_sent_by_source: Dict[str, str]) -> bool:
    """
    last_sent_by_source: {source_key: iso_ts_utc}
    We enforce cooldown by comparing lexicographically ISO timestamps (works for same format).
    """
    if not SOURCE_COOLDOWN_SEC:
        return True
    sk = _source_key(src)
    prev = (last_sent_by_source or {}).get(sk)
    if not prev:
        return True
    try:
        # parse iso-ish with minimal assumptions
        # fallback: if parse fails, don't block
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(prev.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() >= float(SOURCE_COOLDOWN_SEC)
    except Exception:
        return True


# =========================
# Fetch candidate URLs
# =========================
def _fetch_page_text(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            _log(f"fetch {url} -> {r.status_code}")
            return ""
        return r.text or ""
    except Exception as e:
        _log(f"fetch exception {url}: {e}")
        return ""


def _extract_watch_urls_from_html(html: str) -> List[str]:
    """
    Lightweight extraction:
    - find watch?v=VIDEOID patterns and normalize to full watch URL
    - also shorts
    """
    if not html:
        return []

    # find video ids
    vids: List[str] = []

    # watch?v=
    for m in re.finditer(r"watch\?v=([A-Za-z0-9_-]{6,})", html):
        vids.append(m.group(1))

    # shorts/
    for m in re.finditer(r"shorts/([A-Za-z0-9_-]{6,})", html):
        vids.append(m.group(1))

    # keep order, unique
    seen = set()
    out: List[str] = []
    for v in vids:
        if v in seen:
            continue
        seen.add(v)
        out.append(f"https://www.youtube.com/watch?v={v}")
        if len(out) >= MAX_RAW_URLS:
            break
    return out


def _candidate_urls_for_source(src: str) -> List[str]:
    """
    We hit a channel page and parse watch URLs from HTML.
    It's not perfect, but cheap and works without API keys.
    """
    html = _fetch_page_text(src)
    return _extract_watch_urls_from_html(html)


# =========================
# PUBLIC API
# =========================
def get_batch(
    limit: int,
    *,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Returns list of items for feed 'c_youtube':
      {
        "feed": "c_youtube",
        "url": "https://www.youtube.com/watch?v=...",
        "video_id": "...",
        "source": "yt:<video_id>" or "src:<source>",
        "title": "...",
        "ts": int(epoch)
      }

    Key behavior:
      - If a candidate is dead (oEmbed says not alive) -> skip it AND keep searching next.
      - Dedup by video_id and by posted_video_ids.
      - Applies per-source cooldown.
    """
    limit = max(0, int(limit or 0))
    if limit <= 0:
        return []

    sources = _load_sources()
    if not sources:
        return []

    posted_video_ids = set(posted_video_ids or set())
    last_sent_by_source = last_sent_by_source or {}

    out: List[Dict[str, Any]] = []
    seen_vids: Set[str] = set()

    # iterate sources in order; you can shuffle if you want variety
    for src in sources:
        if len(out) >= limit:
            break

        if not _cooldown_ok(src, last_sent_by_source):
            continue

        urls = _candidate_urls_for_source(src)
        _log(f"source {src} candidates={len(urls)}")

        for url in urls:
            if len(out) >= limit:
                break

            u = (url or "").strip()
            if not u or _BAD_URL_RE.search(u):
                continue

            u = _norm_watch_url(u)
            if not u:
                continue

            vid = _extract_video_id(u)
            if not vid:
                continue

            if vid in posted_video_ids or vid in seen_vids:
                continue

            # IMPORTANT: if dead -> skip AND continue searching next
            if not _yt_is_alive(u):
                _log(f"skip dead youtube: {u}")
                seen_vids.add(vid)  # don't re-try same dead id in this run
                continue

            title = _yt_title_oembed(u)  # optional, may be empty

            out.append(
                {
                    "feed": "c_youtube",
                    "url": u,
                    "video_id": vid,
                    "source": f"yt:{vid}",
                    "title": title,
                    "ts": int(time.time()),
                }
            )
            seen_vids.add(vid)

        # if this source produced at least one, we can stop early? no: keep filling from next sources
        # continue to next source until limit is filled

    _log(f"returning={len(out)} limit={limit}")
    return out
