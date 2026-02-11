from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import requests
from openai import OpenAI

# =========================
# CONFIG
# =========================
MODEL = (os.getenv("C_OPENAI_MODEL") or "gpt-4o-mini").strip()

# сколько OpenAI вызовов максимум за один get_batch
MAX_CALLS = int(os.getenv("C_YT_MAX_CALLS", "2"))          # было 3 — жрёт
CANDIDATES_PER_CALL = int(os.getenv("C_YT_CAND_PER_CALL", "6"))  # было 10–12 — жрёт
MAX_URLS_TO_CHECK = int(os.getenv("C_YT_MAX_CHECK", "16")) # верхняя граница на валидацию

# RU/EN поведение
RU_ONLY = (os.getenv("C_YT_RU_ONLY", "0") or "").strip().lower() in {"1", "true", "yes", "on"}
RU_WEIGHT = float(os.getenv("C_YT_RU_WEIGHT", "0.75"))  # доля RU-тем в миксе (если не RU_ONLY)

# Жёсткая фильтрация против концертов/стримов/плейлистов
STRICT_ANTI_LIVE = (os.getenv("C_YT_STRICT_ANTI_LIVE", "1") or "").strip().lower() in {"1", "true", "yes", "on"}

# oEmbed проверка (быстро показывает: видео живое/удалено/приватное)
OEMBED_TIMEOUT = float(os.getenv("C_YT_OEMBED_TIMEOUT", "6"))
HTTP_TIMEOUT = float(os.getenv("C_YT_HTTP_TIMEOUT", "8"))

DEBUG = (os.getenv("C_YT_DEBUG", "1") or "").strip().lower() in {"1", "true", "yes", "on"}

# =========================
# LOG
# =========================
def _log(msg: str) -> None:
    if not DEBUG:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# HELPERS: youtube id / url
# =========================
_YT_ID_RE = re.compile(r"(?:v=|\/shorts\/|youtu\.be\/)([A-Za-z0-9_\-]{6,})")

def _extract_video_id(url: str) -> str:
    u = (url or "").strip()
    m = _YT_ID_RE.search(u)
    if not m:
        return ""
    return (m.group(1) or "").strip()

def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    # убираем мусор в конце
    u = u.split("&pp=")[0]
    u = u.split("&si=")[0]
    u = u.split("?si=")[0]
    u = u.strip()
    return u

def _is_playlist_or_channel(url: str) -> bool:
    u = (url or "").lower()
    if "list=" in u:
        return True
    if "/playlist" in u or "/channel/" in u or "/@" in u and "/video" not in u:
        return True
    return False


# =========================
# CONTENT FILTERS
# =========================
_BAD_WORDS_RU = [
    "концерт", "стрим", "трансляц", "прямой эфир", "полный", "полная версия", "полностью",
    "сборник", "подборка", "плейлист", "альбом", "микс", "час", "часа", "2 часа", "3 часа",
    "live", "лайв", "live session", "full concert", "full show", "full set",
]
_BAD_WORDS_EN = [
    "concert", "full concert", "live", "livestream", "stream", "full show", "full set",
    "playlist", "album", "mix", "compilation", "2 hours", "3 hours", "hour",
    "session", "setlist",
]

def _looks_like_long_or_live(title: str) -> bool:
    t = (title or "").lower()
    if not t:
        return False
    for w in _BAD_WORDS_RU:
        if w in t:
            return True
    for w in _BAD_WORDS_EN:
        if w in t:
            return True
    # грубо: "1:58:00" и т.п.
    if re.search(r"\b\d{1,2}:\d{2}:\d{2}\b", t):
        return True
    return False

def _passes_url_filters(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    lu = u.lower()
    if "youtube.com" not in lu and "youtu.be" not in lu:
        return False
    if _is_playlist_or_channel(u):
        return False
    if STRICT_ANTI_LIVE and ("live" in lu or "stream" in lu):
        # это может быть и короткий live, но ты просил резать жёстко
        return False
    vid = _extract_video_id(u)
    return bool(vid)


# =========================
# OEMBED VALIDATION (fast "dead link" check + title)
# =========================
def _oembed(url: str) -> Optional[dict]:
    # YouTube oEmbed: 200 => ok, 404 => deleted/private/unavailable
    api = "https://www.youtube.com/oembed"
    try:
        r = requests.get(api, params={"url": url, "format": "json"}, timeout=OEMBED_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _validate_and_enrich(url: str) -> Tuple[bool, str]:
    """
    returns (ok, title)
    """
    u = _norm_url(url)
    if not _passes_url_filters(u):
        return (False, "")
    data = _oembed(u)
    if not data:
        return (False, "")
    title = (data.get("title") or "").strip()
    if _looks_like_long_or_live(title):
        return (False, title)
    return (True, title)


# =========================
# THEMES (RU + EN)
# =========================
def _themes_ru() -> List[str]:
    # цель: короткие каверы, не концерты
    return [
        "кавер на русском рок 2023 2024 -концерт -стрим -плейлист -альбом -микс",
        "русский кавер rock cover на русском 2023 2024 -концерт -стрим -плейлист -альбом",
        "метал кавер на русском 2022 2023 -концерт -стрим -плейлист -альбом",
        "рок кавер девушка вокал на русском -концерт -стрим -плейлист",
        "кавер песня на русском 2022 2023 -live -concert -full -playlist -album",
    ]

def _themes_en() -> List[str]:
    return [
        "rock cover live session 2023 2024 -full -concert -playlist -album -mix",
        "metal cover guitar vocal 2022 2023 -full -concert -playlist -album -mix",
        "acoustic rock cover 2023 2024 -full -concert -playlist -album",
    ]

def _pick_theme() -> str:
    ru = _themes_ru()
    en = _themes_en()
    if RU_ONLY:
        return random.choice(ru)
    # микс: чаще RU
    if random.random() < RU_WEIGHT:
        return random.choice(ru)
    return random.choice(en)


# =========================
# OPENAI SEARCH
# =========================
def _openai_find_urls(theme: str, k: int) -> List[str]:
    """
    Просим модель вернуть ТОЛЬКО JSON с urls (без болтовни).
    """
    client = OpenAI()

    sys = (
        "You are a search assistant. Return ONLY valid JSON. No markdown, no explanations."
    )

    # просим отдавать только обычные watch/shorts ссылки, без плейлистов
    user = {
        "task": "find_youtube_urls",
        "query": theme,
        "requirements": [
            "Return only YouTube video URLs (watch?v=... or youtu.be/... or /shorts/...)",
            "Do NOT return playlists or channels",
            "Prefer music covers (кавер/cover). Avoid concerts, live streams, full shows, playlists, mixes.",
            "Return short normal videos (single song), not multi-hour sets.",
        ],
        "count": int(k),
        "output_format": {"items": [{"url": "https://www.youtube.com/watch?v=..."}]},
    }

    resp = client.responses.create(
        model=MODEL,
        input=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    )

    text = (getattr(resp, "output_text", "") or "").strip()
    _log(f"raw_out_len={len(text)} head={text[:120].replace(chr(10),' ')}")

    # парсим JSON максимально терпимо
    data = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None

    urls: List[str] = []

    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    u = _norm_url(it.get("url") or "")
                    if u:
                        urls.append(u)

    # fallback: вытащим ссылки регексом, если модель накосячила
    if not urls:
        for u in re.findall(r"https?://[^\s\"'<>]+", text):
            u = _norm_url(u)
            if u:
                urls.append(u)

    # чистим + дедуп
    out: List[str] = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= k:
            break

    return out


# =========================
# PUBLIC API
# =========================
def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, object]]:
    """
    Returns list of items with fields:
      feed='c_youtube', url, title, video_id, item_id, source, ts
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        _log("OPENAI_API_KEY missing -> returning 0")
        return []

    out: List[Dict[str, object]] = []
    seen_vids: Set[str] = set(posted_video_ids or set())

    calls = 0
    checked = 0
    dead_skipped = 0

    # небольшой “запас” кандидатов: но не бесим OpenAI
    while len(out) < limit and calls < MAX_CALLS and checked < MAX_URLS_TO_CHECK:
        calls += 1
        theme = _pick_theme()
        _log(f"call={calls}/{MAX_CALLS} theme='{theme}'")

        urls = _openai_find_urls(theme, CANDIDATES_PER_CALL)
        _log(f"urls_found={len(urls)}")

        for url in urls:
            if checked >= MAX_URLS_TO_CHECK:
                break

            url = _norm_url(url)
            vid = _extract_video_id(url)
            if not vid:
                continue
            if vid in seen_vids:
                continue

            checked += 1

            ok, title = _validate_and_enrich(url)
            if not ok:
                dead_skipped += 1
                _log(f"skip dead youtube: {url}")
                continue

            # финальная доп. фильтрация по title (на всякий)
            if _looks_like_long_or_live(title):
                dead_skipped += 1
                _log(f"skip long/live title: {url} title='{title[:80]}'")
                continue

            # source — коротко и стабильно
            source = f"yt:{vid}"

            item = {
                "feed": "c_youtube",
                "item_id": vid,
                "video_id": vid,
                "url": url,
                "title": title,
                "source": source,
                "ts": int(time.time()),
            }

            out.append(item)
            seen_vids.add(vid)

            if len(out) >= limit:
                break

    _log(f"returning={len(out)} limit={limit} calls={calls} checked={checked} dead_skipped={dead_skipped}")
    return out
