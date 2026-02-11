from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import requests
from openai import OpenAI

# =========================
# CONFIG
# =========================

# Model for link-finding (keep cheap)
C_OPENAI_MODEL = os.getenv("C_OPENAI_MODEL", "gpt-4o-mini")

# Hard caps to avoid burning money
C_MAX_THEMES_TRIES = int(os.getenv("C_MAX_THEMES_TRIES", "6"))          # how many themes to try per run
C_MAX_CANDIDATES_PER_THEME = int(os.getenv("C_MAX_CANDIDATES", "8"))    # how many url candidates LLM may output
C_MAX_TOTAL_CHECKS = int(os.getenv("C_MAX_TOTAL_CHECKS", "24"))         # total youtube page checks (duration/title)
C_HTTP_TIMEOUT = float(os.getenv("C_HTTP_TIMEOUT", "10"))

# We want "a song", not concerts/sets
C_MAX_DURATION_SEC = int(os.getenv("C_MAX_DURATION_SEC", "720"))        # 12 minutes max
C_MIN_DURATION_SEC = int(os.getenv("C_MIN_DURATION_SEC", "60"))         # at least 1 minute

# how many items we try to collect beyond limit (to survive dead/filtered)
C_OVERFETCH_FACTOR = float(os.getenv("C_OVERFETCH_FACTOR", "2.5"))

# If you want more Russian content bias
C_RU_BIAS = (os.getenv("C_RU_BIAS", "1") or "").strip().lower() in {"1", "true", "yes", "on"}

# Optional: add your preferred style words (comma separated)
C_EXTRA_KEYWORDS = (os.getenv("C_EXTRA_KEYWORDS", "") or "").strip()

# =========================
# LOG
# =========================
def _log(msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# REGEX / FILTERS
# =========================
YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

# Titles we DO NOT want (concerts, mixes, full albums, etc.)
BAD_TITLE_RE = re.compile(
    r"\b("
    r"full\s*concert|concert|live\s*session|live|session|full\s*show|full\s*set|set\s*list|"
    r"playlist|album|mix|compilation|dj\s*set|"
    r"full\s*album|full\s*video|"
    r"\d+\s*hours?|hours?\s*\d+|"
    r"час(а|ов)?|"
    r")\b",
    re.IGNORECASE,
)

# Better when title contains these for covers
GOOD_HINT_RE = re.compile(r"\b(cover|кавер|перепел|перепела|cover version)\b", re.IGNORECASE)


def _extract_video_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = YT_ID_RE.search(url)
    if not m:
        return None
    return m.group(1)


def _normalize_watch_url(url: str) -> Optional[str]:
    vid = _extract_video_id(url)
    if not vid:
        return None
    return f"https://www.youtube.com/watch?v={vid}"


def _looks_like_youtube(url: str) -> bool:
    u = (url or "").strip().lower()
    return "youtube.com/" in u or "youtu.be/" in u


def _http_get(url: str) -> Optional[str]:
    try:
        r = requests.get(
            url,
            timeout=C_HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
            },
        )
        if r.status_code != 200:
            return None
        return r.text or ""
    except Exception:
        return None


def _parse_title(html: str) -> str:
    if not html:
        return ""
    # og:title is the cleanest
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    if m:
        return (m.group(1) or "").strip()
    # fallback <title>
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        t = t.replace(" - YouTube", "").strip()
        return t
    return ""


def _parse_length_seconds(html: str) -> Optional[int]:
    if not html:
        return None

    # common: "lengthSeconds":"123"
    m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass

    # sometimes: "approxDurationMs":"123000"
    m = re.search(r'"approxDurationMs"\s*:\s*"(\d+)"', html)
    if m:
        try:
            ms = int(m.group(1))
            return max(0, ms // 1000)
        except Exception:
            pass

    return None


def _is_dead_or_unavailable(html: str) -> bool:
    if not html:
        return True
    t = html.lower()
    # RU/EN unavailable markers
    return (
        "video is unavailable" in t
        or "this video is not available" in t
        or "это видео больше не доступно" in t
        or "video unavailable" in t
        or "sign in to confirm your age" in t  # age-gated -> we skip
    )


def _video_ok(url: str, *, require_russian: bool) -> Optional[dict]:
    """
    Fetch watch page; reject dead/unavailable; parse title + duration; apply filters.
    Returns dict with title, duration_sec if OK, else None.
    """
    watch = _normalize_watch_url(url)
    if not watch:
        return None

    html = _http_get(watch)
    if not html or _is_dead_or_unavailable(html):
        return None

    title = _parse_title(html)
    dur = _parse_length_seconds(html)

    # Hard filter by title keywords
    if title and BAD_TITLE_RE.search(title):
        return None

    # Must look like a cover (hint in title OR later we allow if prompt already "cover")
    if title and not GOOD_HINT_RE.search(title):
        # not a strict fail: sometimes title doesn't contain "cover" but still is
        # however to avoid concerts we keep it as soft requirement unless russian is required
        if require_russian:
            return None

    # Russian requirement (for RU themes)
    if require_russian:
        if not title or not CYRILLIC_RE.search(title):
            return None

    # Duration filter
    if dur is not None:
        if dur < C_MIN_DURATION_SEC or dur > C_MAX_DURATION_SEC:
            return None

    return {"title": title or "", "duration_sec": dur}


# =========================
# THEMES (NO HUGE LIST)
# =========================
def _themes() -> List[str]:
    base = [
        # works globally
        "rock cover song",
        "metal cover song",
        "acoustic cover song",
        "guitar cover song",
        "female vocal cover",
        "male vocal cover",
    ]

    # Russian bias: IMPORTANT — use EN keyword "cover"
    ru = [
        "cover на русском песня",
        "rock cover на русском",
        "metal cover на русском",
        "песня cover на русском",
    ]

    extra = []
    if C_EXTRA_KEYWORDS:
        # allow user to inject style hints, but keep safe
        for x in re.split(r"[,\n;]+", C_EXTRA_KEYWORDS):
            x = (x or "").strip()
            if x:
                extra.append(x)

    out = ru + base if C_RU_BIAS else base + ru
    out.extend(extra)

    # randomize a bit so it doesn't get stuck
    random.shuffle(out)
    return out


# =========================
# OPENAI: find candidate URLs
# =========================
def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def _llm_find_urls(theme: str, max_urls: int) -> List[str]:
    if not _has_key():
        return []

    client = OpenAI()

    # VERY IMPORTANT: forbid concerts, playlists, full shows, mixes
    # ask for short songs only
    prompt = (
        "Find YouTube links that match this theme:\n"
        f"THEME: {theme}\n\n"
        "Rules:\n"
        "- Return ONLY normal YouTube watch links (https://www.youtube.com/watch?v=VIDEO_ID).\n"
        "- Prefer single songs (2-8 minutes), NOT concerts, NOT full live sessions, NOT playlists, NOT mixes.\n"
        "- Prefer covers.\n"
        "- Avoid: concert, live session, full concert, playlist, album, mix.\n"
        f"- Return up to {max_urls} links.\n\n"
        'Output strictly as JSON: {"urls":[...]}'
    )

    try:
        resp = client.responses.create(
            model=C_OPENAI_MODEL,
            input=[{"role": "user", "content": prompt}],
        )
        out = getattr(resp, "output_text", "") or ""
    except Exception as e:
        _log(f"openai error: {type(e).__name__}: {e}")
        return []

    # parse JSON
    urls: List[str] = []
    try:
        data = json.loads(out)
        arr = data.get("urls") or []
        if isinstance(arr, list):
            urls = [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        # fallback: extract any youtube URLs from raw text
        urls = re.findall(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=[A-Za-z0-9_-]{11}|youtu\.be/[A-Za-z0-9_-]{11})", out)

    # normalize + unique keep order
    seen = set()
    cleaned: List[str] = []
    for u in urls:
        if not _looks_like_youtube(u):
            continue
        w = _normalize_watch_url(u)
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        cleaned.append(w)

    return cleaned[: max(0, max_urls)]


# =========================
# PUBLIC API
# =========================
def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[dict]:
    """
    Returns list of dicts:
      {
        "feed": "c_youtube",
        "url": "...",
        "video_id": "...",
        "source": "yt:<video_id>",
        "title": "...",
        "ts": int
      }
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    themes = _themes()
    max_themes = min(len(themes), C_MAX_THEMES_TRIES)

    need = limit
    want_candidates = int(max(6, min(C_MAX_TOTAL_CHECKS, limit * C_OVERFETCH_FACTOR * 4)))

    picked: List[dict] = []
    checked = 0
    dead_skipped = 0

    # try several themes; for RU themes require Cyrillic in title
    for ti in range(max_themes):
        if len(picked) >= need:
            break
        if checked >= C_MAX_TOTAL_CHECKS:
            break

        theme = themes[ti]

        require_russian = False
        if "на русском" in theme or "рус" in theme:
            require_russian = True

        # keep LLM output small (money)
        urls = _llm_find_urls(theme, max_urls=min(C_MAX_CANDIDATES_PER_THEME, want_candidates))
        _log(f"call={ti+1}/{max_themes} urls_found={len(urls)} theme='{theme}'")

        for url in urls:
            if len(picked) >= need:
                break
            if checked >= C_MAX_TOTAL_CHECKS:
                break

            vid = _extract_video_id(url)
            if not vid:
                continue
            if vid in posted_video_ids:
                continue

            checked += 1
            info = _video_ok(url, require_russian=require_russian)
            if not info:
                dead_skipped += 1
                _log(f"skip dead/filtered youtube: {url}")
                continue

            title = (info.get("title") or "").strip()
            picked.append(
                {
                    "feed": "c_youtube",
                    "url": _normalize_watch_url(url),
                    "video_id": vid,
                    "source": f"yt:{vid}",
                    "title": title,
                    "ts": int(time.time()),
                }
            )

            posted_video_ids.add(vid)

        # small jitter so we don't hammer
        time.sleep(0.2)

    _log(f"returning={len(picked)} limit={limit} checked={checked} dead_skipped={dead_skipped}")
    return picked[:limit]
