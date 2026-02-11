from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests


# =========================
# CONFIG
# =========================
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEAD_CACHE_JSON = DATA_DIR / "yt_dead_cache.json"
DEAD_TTL_SEC = int(os.getenv("C_YT_DEAD_TTL_SEC", "86400"))  # 24h default

HTTP_TIMEOUT = float(os.getenv("C_YT_TIMEOUT_SEC", "12"))
MAX_URLS_PER_QUERY = int(os.getenv("C_YT_MAX_URLS_PER_QUERY", "18"))  # кандидатов из HTML
MAX_QUERIES_PER_CALL = int(os.getenv("C_YT_MAX_QUERIES_PER_CALL", "3"))  # сколько тем пробовать за один get_batch

DEBUG = (os.getenv("C_YT_DEBUG", "1") or "").strip().lower() in {"1", "true", "yes", "on"}

UA = os.getenv(
    "C_YT_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
)

# Темы. Можно править через env, но дефолт максимально “каверный”.
DEFAULT_THEMES = [
    "rock cover live session 2023 2024",
    "metal cover guitar vocal 2022 2023",
    "post-hardcore cover acoustic rock",
    "female vocal rock cover live 2023 2024",
    "guitar cover live session",
]

THEMES_ENV = (os.getenv("C_YT_THEMES") or "").strip()
THEMES = [t.strip() for t in THEMES_ENV.split("|") if t.strip()] if THEMES_ENV else DEFAULT_THEMES

# Фильтр по заголовку (чтобы “не кавер” меньше пролезал)
TITLE_MUST_HAVE = re.compile(r"\b(cover|кавер|live session|live|session)\b", re.IGNORECASE)


def _dbg(msg: str) -> None:
    if not DEBUG:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# DEAD CACHE
# =========================
def _load_dead_cache() -> Dict[str, float]:
    if not DEAD_CACHE_JSON.exists():
        return {}
    try:
        d = json.loads(DEAD_CACHE_JSON.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            out: Dict[str, float] = {}
            for k, v in d.items():
                try:
                    out[str(k)] = float(v)
                except Exception:
                    pass
            return out
    except Exception:
        pass
    return {}


def _save_dead_cache(d: Dict[str, float]) -> None:
    try:
        DEAD_CACHE_JSON.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _is_dead_cached(dead: Dict[str, float], vid: str) -> bool:
    ts = dead.get(vid)
    if not ts:
        return False
    return (time.time() - ts) <= DEAD_TTL_SEC


def _mark_dead(dead: Dict[str, float], vid: str) -> None:
    dead[vid] = time.time()


# =========================
# SCRAPE + VERIFY
# =========================
_VID_RE = re.compile(r"watch\?v=([A-Za-z0-9_-]{11})")


def _search_video_ids(query: str) -> List[str]:
    """
    Достаём видео-id из HTML страницы результатов.
    """
    q = (query or "").strip()
    if not q:
        return []

    url = "https://www.youtube.com/results"
    params = {"search_query": q}

    try:
        r = requests.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"},
        )
    except Exception as e:
        _dbg(f"search request error: {type(e).__name__}: {e}")
        return []

    if r.status_code != 200:
        _dbg(f"search http={r.status_code}")
        return []

    html = r.text or ""
    ids = _VID_RE.findall(html)

    # dedupe keep order
    out: List[str] = []
    seen: Set[str] = set()
    for vid in ids:
        if vid in seen:
            continue
        seen.add(vid)
        out.append(vid)
        if len(out) >= MAX_URLS_PER_QUERY:
            break

    return out


def _oembed_title(vid: str) -> Optional[str]:
    """
    Валидация + получение title.
    Если oEmbed не отдаёт — считаем “мертвым/закрытым/недоступным”.
    """
    watch = f"https://www.youtube.com/watch?v={vid}"
    oembed = "https://www.youtube.com/oembed"
    try:
        r = requests.get(
            oembed,
            params={"url": watch, "format": "json"},
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"},
        )
    except Exception:
        return None

    if r.status_code != 200:
        return None

    try:
        data = r.json()
    except Exception:
        return None

    title = (data.get("title") or "").strip()
    return title or None


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
    Возвращает items ДЛЯ main.py:
    {
      "feed": "c_youtube",
      "url": "...",
      "video_id": "...",
      "title": "...",
      "source": "q:<slug>",
      "ts": int
    }
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    posted_video_ids = set(posted_video_ids or set())
    last_sent_by_source = dict(last_sent_by_source or {})

    dead = _load_dead_cache()
    now_ts = int(time.time())

    out: List[Dict[str, Any]] = []
    dead_skipped = 0
    checked = 0

    # сколько тем попробуем за один вызов
    themes = THEMES[: max(1, MAX_QUERIES_PER_CALL)]

    for idx, theme in enumerate(themes, start=1):
        if len(out) >= limit:
            break

        theme = (theme or "").strip()
        if not theme:
            continue

        _dbg(f"call={idx}/{len(themes)} theme={theme!r}")

        vids = _search_video_ids(theme)
        _dbg(f"urls_found={len(vids)}")

        # slug для source (чтобы можно было “cooldown по теме” сделать при желании)
        slug = re.sub(r"[^a-z0-9]+", "_", theme.lower()).strip("_")
        source = f"q:{slug}" if slug else "q:default"

        for vid in vids:
            if len(out) >= limit:
                break

            if not vid or len(vid) != 11:
                continue

            if vid in posted_video_ids:
                continue

            if _is_dead_cached(dead, vid):
                dead_skipped += 1
                continue

            checked += 1
            title = _oembed_title(vid)
            if not title:
                _mark_dead(dead, vid)
                dead_skipped += 1
                _dbg(f"skip dead youtube: https://www.youtube.com/watch?v={vid}")
                continue

            # грубый фильтр “это кавер/лайв”
            if not TITLE_MUST_HAVE.search(title):
                # НЕ помечаем dead, просто нерелевантно
                continue

            out.append(
                {
                    "feed": "c_youtube",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "video_id": vid,
                    "title": title,
                    "source": source,
                    "ts": now_ts,
                }
            )

        # сохраняем кэш после каждой темы (чтобы при падениях не терять)
        _save_dead_cache(dead)

    _dbg(f"returning={len(out)} limit={limit} checked={checked} dead_skipped={dead_skipped}")
    return out[:limit]
