from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests
from openai import OpenAI

# =========================
# CONFIG
# =========================
C_DEBUG = (os.getenv("C_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}

C_MODEL = os.getenv("C_OPENAI_MODEL", os.getenv("C_MODEL", "gpt-4o-mini")).strip() or "gpt-4o-mini"
C_TIMEOUT_SEC = float(os.getenv("C_OPENAI_TIMEOUT_SEC", "45"))

# How many attempts (OpenAI calls) per get_batch
C_MAX_CALLS = int(os.getenv("C_YT_MAX_CALLS", "3"))

# Candidates per OpenAI call (DON'T inflate; keep small)
C_URLS_PER_CALL = int(os.getenv("C_YT_URLS_PER_CALL", "6"))

# Hard requirement: title must include these tokens to be considered a "cover"
COVER_TOKENS_RE = re.compile(r"\b(cover|covers|covering|кавер|каверы)\b", re.IGNORECASE)

# If title missing from LLM output, try to resolve via oEmbed (cheap)
USE_OEMBED = (os.getenv("C_YT_USE_OEMBED", "1") or "").strip().lower() in {"1", "true", "yes", "on"}
OEMBED_TIMEOUT = float(os.getenv("C_YT_OEMBED_TIMEOUT_SEC", "8"))

# Add 1-2 fallback items at the end if still short (unverified-by-title, but only if oEmbed confirms cover tokens)
FALLBACK_UNVERIFIED_ADD = int(os.getenv("C_YT_FALLBACK_UNVERIFIED_ADD", "2"))

# Themes (rotate)
THEMES = [
    "rock cover live session 2023 2024",
    "metal cover guitar vocal 2022 2023",
    "post-hardcore cover acoustic rock",
    "alt rock cover live",
    "punk rock cover live",
]


# =========================
# LOG
# =========================
def _log(msg: str) -> None:
    if C_DEBUG:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# HELPERS
# =========================
_YT_ID_RE = re.compile(r"(?:v=|\/shorts\/|youtu\.be\/)([A-Za-z0-9_-]{6,})")


def _extract_video_id(url: str) -> str:
    u = (url or "").strip()
    m = _YT_ID_RE.search(u)
    return (m.group(1) if m else "").strip()


def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    # normalize to canonical watch URL if we have id
    vid = _extract_video_id(u)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return u


def _oembed_title(url: str) -> Optional[str]:
    if not USE_OEMBED:
        return None
    u = _norm_url(url)
    if not u:
        return None
    # YouTube oEmbed endpoint (no key)
    oembed = "https://www.youtube.com/oembed"
    try:
        r = requests.get(
            oembed,
            params={"url": u, "format": "json"},
            timeout=OEMBED_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        t = (data.get("title") or "").strip()
        return t or None
    except Exception:
        return None


def _looks_like_cover(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    return bool(COVER_TOKENS_RE.search(t))


def _parse_items_from_llm(raw: str) -> List[Dict[str, Any]]:
    """
    Expected JSON:
    { "items": [ {"url":"...", "title":"...", "source":"..."} ... ] }
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        # try extract JSON object
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []

    items = data.get("items")
    if not isinstance(items, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = _norm_url(str(it.get("url") or "").strip())
        if not url:
            continue
        title = str(it.get("title") or "").strip()
        source = str(it.get("source") or "").strip()
        out.append({"url": url, "title": title, "source": source})
    return out


def _call_llm(theme: str, n: int) -> List[Dict[str, Any]]:
    client = OpenAI()

    prompt = (
        "Найди реальные ссылки YouTube на КАВЕРЫ (обязательно cover/кавер в названии).\n"
        "Тема запроса: " + theme + "\n\n"
        f"Верни строго JSON: {{\"items\":[{{\"url\":\"...\",\"title\":\"...\",\"source\":\"...\"}}]}}\n"
        f"- items длиной до {n}\n"
        "- url только youtube.com или youtu.be\n"
        "- title заполняй, не оставляй пустым\n"
        "- source: короткий идентификатор (например 'yt:' + video_id)\n"
        "Никаких пояснений. Только JSON."
    )

    resp = client.responses.create(
        model=C_MODEL,
        input=[{"role": "user", "content": prompt}],
        timeout=C_TIMEOUT_SEC,
    )

    # openai python SDK обычно даёт output_text
    raw = (getattr(resp, "output_text", "") or "").strip()
    _log(f"raw_out_len={len(raw)} head={raw[:120]!r}")
    return _parse_items_from_llm(raw)


# =========================
# PUBLIC API
# =========================
def get_batch(
    limit: int,
    *,
    posted_video_ids: Optional[Set[str]] = None,
    last_sent_by_source: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Returns list of items for main.py _send_one() "c_youtube" path:
      {
        "feed": "c_youtube",
        "item_id": "<video_id>",
        "video_id": "<video_id>",
        "url": "...",
        "title": "...",
        "source": "yt:<video_id>"
      }
    """
    posted_video_ids = posted_video_ids or set()
    last_sent_by_source = last_sent_by_source or {}

    limit = max(0, int(limit))
    if limit <= 0:
        return []

    out: List[Dict[str, Any]] = []
    seen_vid: Set[str] = set()

    calls = 0
    theme_i = 0

    dead_skipped = 0

    while calls < C_MAX_CALLS and len(out) < limit:
        theme = THEMES[theme_i % len(THEMES)]
        theme_i += 1
        calls += 1

        items = _call_llm(theme, C_URLS_PER_CALL)
        _log(f"call={calls}/{C_MAX_CALLS} urls_found={len(items)} theme={theme!r}")

        for cand in items:
            if len(out) >= limit:
                break

            url = (cand.get("url") or "").strip()
            vid = _extract_video_id(url)
            if not vid:
                dead_skipped += 1
                continue

            if vid in posted_video_ids or vid in seen_vid:
                continue

            title = (cand.get("title") or "").strip()

            # If title missing, try oEmbed (cheap)
            if not title:
                title = _oembed_title(url) or ""

            # Must look like a cover
            if not _looks_like_cover(title):
                # one more chance via oEmbed if it wasn't used and title not cover-like
                if USE_OEMBED:
                    t2 = _oembed_title(url)
                    if t2:
                        title = t2
                if not _looks_like_cover(title):
                    dead_skipped += 1
                    _log(f"skip not cover: {url}")
                    continue

            # final normalize
            url = _norm_url(url)
            seen_vid.add(vid)

            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": url,
                    "title": title,
                    "source": f"yt:{vid}",
                    "ts": int(time.time()),
                }
            )

        # If we are starving, allow small fallback: accept candidates with oEmbed-confirmed cover tokens even if LLM title was junk
        if len(out) < limit and USE_OEMBED and FALLBACK_UNVERIFIED_ADD > 0:
            # nothing extra here; the above already oEmbed-resolves.
            pass

    _log(f"returning={len(out)} limit={limit} calls={calls} dead_skipped={dead_skipped}")
    return out
