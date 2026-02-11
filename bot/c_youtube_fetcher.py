from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from typing import Dict, List, Optional, Set


# =========================
# CONFIG
# =========================
C_MAX_DURATION_SEC = int(os.getenv("C_MAX_DURATION_SEC", "540"))   # 9 min (чтоб не концерты)
C_MIN_DURATION_SEC = int(os.getenv("C_MIN_DURATION_SEC", "75"))    # 1m15s (чтоб не шорты/интро)
C_YTSEARCH_PER_QUERY = int(os.getenv("C_YTSEARCH_PER_QUERY", "10"))  # сколько кандидатов брать на запрос
C_MAX_QUERIES_PER_RUN = int(os.getenv("C_MAX_QUERIES_PER_RUN", "6")) # сколько разных запросов пробовать
C_REQUIRE_RU = (os.getenv("C_REQUIRE_RU", "1") or "").strip().lower() in {"1", "true", "yes", "on"}

# если хочешь иногда английские — поставь C_REQUIRE_RU=0
# можно доп. ключи через env (например: "female vocal, acoustic")
C_EXTRA_KEYWORDS = (os.getenv("C_EXTRA_KEYWORDS", "") or "").strip()

# =========================
# LOG
# =========================
def _log(msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# FILTERS
# =========================
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

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

GOOD_HINT_RE = re.compile(r"\b(cover|кавер|cover version|перепел|перепела)\b", re.IGNORECASE)


def _normalize_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _themes() -> List[str]:
    # ВАЖНО: на YouTube лучше работает слово "cover" (англ), даже для русских каверов.
    base_ru = [
        "кавер на русском cover",
        "cover на русском песня",
        "rock cover на русском",
        "metal cover на русском",
        "acoustic cover на русском",
        "guitar cover на русском",
        "женский вокал cover на русском",
    ]

    base_en = [
        "rock cover song",
        "metal cover song",
        "acoustic cover song",
        "guitar cover song",
        "female vocal cover",
        "male vocal cover",
    ]

    extra = []
    if C_EXTRA_KEYWORDS:
        for x in re.split(r"[,\n;]+", C_EXTRA_KEYWORDS):
            x = (x or "").strip()
            if x:
                extra.append(x)

    # если требуем RU — сначала RU темы, иначе микс
    out = (base_ru + base_en) if C_REQUIRE_RU else (base_en + base_ru)
    out.extend(extra)

    random.shuffle(out)
    return out


def _yt_dlp_search(query: str, n: int) -> List[dict]:
    """
    Uses yt-dlp ytsearch. Returns list of entry dicts (id, title, duration, is_live, webpage_url).
    Requires yt-dlp installed.
    """
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        f"ytsearch{n}:{query}",
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        _log(f"yt-dlp spawn error: {type(e).__name__}: {e}")
        return []

    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[:300].replace("\n", " ")
        _log(f"yt-dlp error rc={r.returncode} q='{query}' err='{err}'")
        return []

    try:
        data = json.loads(r.stdout or "{}")
    except Exception:
        _log("yt-dlp returned bad json")
        return []

    entries = data.get("entries") or []
    if not isinstance(entries, list):
        return []

    out: List[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        vid = (e.get("id") or "").strip()
        if not vid:
            continue
        out.append(e)
    return out


def _entry_ok(e: dict, *, require_ru: bool) -> Optional[dict]:
    """
    Returns normalized item dict if OK, else None.
    """
    vid = (e.get("id") or "").strip()
    if not vid:
        return None

    title = (e.get("title") or "").strip()
    duration = e.get("duration")
    is_live = bool(e.get("is_live") or (e.get("live_status") in {"is_live", "live"}))

    if is_live:
        return None

    if title and BAD_TITLE_RE.search(title):
        return None

    # хотим именно каверы (иначе будет мусор типа "trailer", "teaser", "full show")
    if title and not GOOD_HINT_RE.search(title):
        return None

    if require_ru:
        if not title or not CYRILLIC_RE.search(title):
            return None

    # duration must exist for reliable filtering
    try:
        d = int(duration) if duration is not None else None
    except Exception:
        d = None

    if d is None:
        return None

    if d < C_MIN_DURATION_SEC or d > C_MAX_DURATION_SEC:
        return None

    return {
        "feed": "c_youtube",
        "url": _normalize_watch_url(vid),
        "video_id": vid,
        "source": f"yt:{vid}",
        "title": title,
        "ts": int(time.time()),
    }


def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[dict]:
    """
    Returns list of dicts compatible with main.py _send_one() branch for feed=c_youtube.
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    themes = _themes()
    max_q = min(len(themes), C_MAX_QUERIES_PER_RUN)

    picked: List[dict] = []
    checked = 0
    accepted = 0

    for qi in range(max_q):
        if len(picked) >= limit:
            break

        q = themes[qi]
        _log(f"query={qi+1}/{max_q} ytsearch={C_YTSEARCH_PER_QUERY} theme='{q}'")

        entries = _yt_dlp_search(q, C_YTSEARCH_PER_QUERY)
        _log(f"urls_found={len(entries)}")

        for e in entries:
            if len(picked) >= limit:
                break

            vid = (e.get("id") or "").strip()
            if not vid:
                continue
            if vid in posted_video_ids:
                continue

            checked += 1
            it = _entry_ok(e, require_ru=C_REQUIRE_RU)
            if not it:
                continue

            picked.append(it)
            posted_video_ids.add(vid)
            accepted += 1

        # лёгкая пауза
        time.sleep(0.1)

    _log(f"returning={len(picked)} limit={limit} checked={checked} accepted={accepted}")
    return picked[:limit]
