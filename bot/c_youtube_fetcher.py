from __future__ import annotations

import os
import re
import time
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from openai import OpenAI

# =========================
# CONFIG
# =========================
C_OPENAI_MODEL = (os.getenv("C_OPENAI_MODEL") or "gpt-4o-mini").strip()
C_OPENAI_TIMEOUT_SEC = float(os.getenv("C_OPENAI_TIMEOUT_SEC", "45"))

C_DEBUG = (os.getenv("C_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}

# limit how many candidate URLs we even consider (keeps OpenAI + checks cheap)
MAX_CANDIDATES_PER_CALL = int(os.getenv("C_MAX_CANDIDATES_PER_CALL", "6"))  # keep small

# total tries (OpenAI calls): 1..2
MAX_CALLS_PER_BATCH = int(os.getenv("C_MAX_CALLS_PER_BATCH", "2"))

# verify links quickly via YouTube oEmbed
VERIFY_TIMEOUT_SEC = float(os.getenv("C_VERIFY_TIMEOUT_SEC", "8"))
VERIFY_UA = os.getenv(
    "C_VERIFY_UA",
    "Mozilla/5.0 (compatible; tg_pipeline_bot/1.0; +https://example.invalid)",
)

# cooldown per source (in hours) to not spam same theme/source
SOURCE_COOLDOWN_HOURS = float(os.getenv("C_SOURCE_COOLDOWN_HOURS", "18"))

# themes (keep short)
DEFAULT_THEMES = [
    "rock cover live session 2023 2024",
    "metal cover guitar vocal 2022 2023",
    "post-hardcore cover acoustic rock",
    "female rock cover guitar vocal 2022 2023",
]

# =========================
# LOGGING
# =========================
def _log(msg: str) -> None:
    if C_DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# URL / ID utils
# =========================
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_URL_RE = re.compile(r"https?://[^\s)>\"]+", re.IGNORECASE)
_YT_WATCH_RE = re.compile(r"(?:youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,20})", re.IGNORECASE)
_YT_SHORT_RE = re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{6,20})", re.IGNORECASE)
_YT_SHORTS_RE = re.compile(r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{6,20})", re.IGNORECASE)


def _extract_video_id(url: str) -> str:
    u = (url or "").strip()
    m = _YT_WATCH_RE.search(u)
    if m:
        return m.group(1)
    m = _YT_SHORT_RE.search(u)
    if m:
        return m.group(1)
    m = _YT_SHORTS_RE.search(u)
    if m:
        return m.group(1)
    # if someone gives raw id
    if _YT_ID_RE.fullmatch(u):
        return u
    return ""


def _normalize_youtube_url(url: str) -> str:
    u = (url or "").strip()
    vid = _extract_video_id(u)
    if not vid:
        return ""
    return f"https://www.youtube.com/watch?v={vid}"


def _oembed_ok(url: str) -> Tuple[bool, str]:
    """
    Returns (ok, title_from_oembed_or_empty)
    200 => ok
    401/403/404 => not ok (private/deleted/unavailable)
    """
    try:
        oembed = "https://www.youtube.com/oembed"
        params = {"url": url, "format": "json"}
        r = requests.get(
            oembed,
            params=params,
            timeout=VERIFY_TIMEOUT_SEC,
            headers={"User-Agent": VERIFY_UA},
        )
        if r.status_code == 200:
            try:
                data = r.json()
                title = (data.get("title") or "").strip()
            except Exception:
                title = ""
            return True, title
        return False, ""
    except Exception:
        return False, ""


def _now_ts() -> int:
    return int(time.time())


def _cooldown_ok(source: str, last_sent_by_source: Dict[str, str]) -> bool:
    """
    last_sent_by_source: {source_lower: iso_ts}
    We keep it simple: compare lexicographically if ISO.
    """
    src = (source or "").strip().lower()
    if not src:
        return True
    last_iso = (last_sent_by_source or {}).get(src)
    if not last_iso:
        return True
    try:
        # parse like "2026-02-11T09:09:17.123+00:00" or "...Z"
        # avoid heavy deps; do minimal
        # fallback: if fails -> don't block
        from datetime import datetime, timezone

        s = last_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        return age_hours >= SOURCE_COOLDOWN_HOURS
    except Exception:
        return True


# =========================
# OpenAI prompt / parsing
# =========================
_SYSTEM = """
You generate YouTube video recommendations.
Return STRICT JSON only, no markdown.

Schema:
{
  "items": [
    {"url": "https://www.youtube.com/watch?v=VIDEO_ID", "title": "short title"}
  ]
}

Rules:
- ONLY YouTube links.
- Prefer music performance/cover/live sessions for the given theme.
- Provide 4-8 items.
- Title may be empty if unknown.
"""


def _call_openai(theme: str, *, n_max: int) -> List[Dict[str, str]]:
    """
    returns list of dicts with url/title (possibly messy; we will normalize + validate)
    """
    api_key_set = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    if not api_key_set:
        _log("OPENAI_API_KEY is unset -> return []")
        return []

    client = OpenAI(timeout=C_OPENAI_TIMEOUT_SEC)

    user = f"""
Theme: {theme}

Give me 6-8 YouTube video links matching this theme.
Remember: STRICT JSON only with schema: {{ "items": [{{"url":"...","title":"..."}}] }}.
"""

    resp = client.responses.create(
        model=C_OPENAI_MODEL,
        input=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    )

    # Try to read output_text; then parse JSON; else extract urls and wrap
    out = (getattr(resp, "output_text", "") or "").strip()
    _log(f"raw_out_len={len(out)} head={out[:120]!r}")

    items: List[Dict[str, str]] = []

    # JSON first
    try:
        data = json.loads(out)
        raw_items = data.get("items") or []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            url = (it.get("url") or "").strip()
            title = (it.get("title") or "").strip()
            if url:
                items.append({"url": url, "title": title})
    except Exception:
        # fallback: regex URLs
        for m in _URL_RE.finditer(out):
            u = m.group(0)
            if "youtu" in u.lower():
                items.append({"url": u, "title": ""})

    # cap
    if n_max > 0:
        items = items[:n_max]
    return items


# =========================
# PUBLIC API
# =========================
def get_batch(
    limit: int,
    *,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
    themes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Returns list of items for main.py send_batch()

    Each item:
    {
      "feed": "c_youtube",
      "item_id": "<video_id>",
      "video_id": "<video_id>",
      "url": "https://www.youtube.com/watch?v=...",
      "title": "...",
      "source": "yt:<video_id>",
      "ts": <unix_ts_int>
    }
    """
    try:
        lim = max(0, int(limit))
    except Exception:
        lim = 0
    if lim <= 0:
        return []

    themes = [t.strip() for t in (themes or DEFAULT_THEMES) if (t or "").strip()]
    if not themes:
        themes = DEFAULT_THEMES[:]

    # filter themes by cooldown (we treat theme as "source" bucket)
    usable_themes = []
    for t in themes:
        src_key = f"theme:{t.lower()}"
        if _cooldown_ok(src_key, last_sent_by_source):
            usable_themes.append(t)

    if not usable_themes:
        usable_themes = themes[:]  # if all cooled down, ignore cooldown

    # We will do up to MAX_CALLS_PER_BATCH calls and validate sequentially
    out: List[Dict[str, Any]] = []
    seen_vids: Set[str] = set()
    posted_video_ids = set(posted_video_ids or set())

    calls = 0
    theme_idx = 0

    fallback_unverified: List[Dict[str, Any]] = []
    skip_dead = 0

    while len(out) < lim and calls < max(1, MAX_CALLS_PER_BATCH) and theme_idx < len(usable_themes):
        theme = usable_themes[theme_idx]
        theme_idx += 1
        calls += 1

        candidates = _call_openai(theme, n_max=MAX_CANDIDATES_PER_CALL)
        _log(f"call={calls}/{MAX_CALLS_PER_BATCH} urls_found={len(candidates)} theme={theme!r}")

        for cand in candidates:
            if len(out) >= lim:
                break

            url0 = (cand.get("url") or "").strip()
            title0 = (cand.get("title") or "").strip()

            url = _normalize_youtube_url(url0)
            vid = _extract_video_id(url)
            if not url or not vid:
                continue

            if vid in posted_video_ids or vid in seen_vids:
                continue

            seen_vids.add(vid)

            ok, t_from = _oembed_ok(url)
            if not ok:
                skip_dead += 1
                _log(f"skip dead youtube: {url}")
                continue

            title = title0 or t_from or ""
            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": url,
                    "title": title,
                    "source": f"yt:{vid}",
                    "ts": _now_ts(),
                }
            )

        # collect some unverified fallback from remaining candidates (cheap safety net)
        if len(out) < lim:
            for cand in candidates:
                url = _normalize_youtube_url((cand.get("url") or "").strip())
                vid = _extract_video_id(url)
                if not url or not vid:
                    continue
                if vid in posted_video_ids or vid in seen_vids:
                    continue
                seen_vids.add(vid)
                fallback_unverified.append(
                    {
                        "feed": "c_youtube",
                        "item_id": vid,
                        "video_id": vid,
                        "url": url,
                        "title": (cand.get("title") or "").strip(),
                        "source": f"yt:{vid}",
                        "ts": _now_ts(),
                    }
                )
                if len(fallback_unverified) >= (lim * 3):
                    break

    # If still not enough, add a couple of unverified (but not dead-checked)
    if len(out) < lim and fallback_unverified:
        need = lim - len(out)
        add = fallback_unverified[:need]
        if add:
            _log(f"fallback_unverified_added={len(add)}")
            out.extend(add)

    _log(f"returning={len(out)} limit={lim} calls={calls} dead_skipped={skip_dead}")
    return out
