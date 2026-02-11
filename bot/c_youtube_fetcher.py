from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Set, Any, Optional

import yt_dlp


# =========================
# CONFIG
# =========================

# Сколько ссылок нужно вернуть (limit приходит из main.py)
# Сколько кандидатов брать на один запрос (чем больше — тем чаще будут "мертвые"/левые)
PER_QUERY = int(os.getenv("C_YT_PER_QUERY", "8"))

# Сколько разных запросов прогоняем за один get_batch (чтобы не улететь в вечный цикл)
MAX_QUERIES_PER_BATCH = int(os.getenv("C_YT_MAX_QUERIES_PER_BATCH", "6"))

# Максимальная длительность ролика (сек). Чтобы не присылало концерты/сеты.
MAX_DURATION_SEC = int(os.getenv("C_YT_MAX_DURATION_SEC", "480"))  # 8 минут по умолчанию

# Минимальная длительность, чтобы не присылать 5-сек мусор
MIN_DURATION_SEC = int(os.getenv("C_YT_MIN_DURATION_SEC", "40"))

# Жёсткий стоп-лист по словам (в заголовке)
STOP_WORDS = [
    "full concert",
    "full show",
    "full album",
    "full set",
    "playlist",
    "mix",
    "compilation",
    "session",
    "live session",
    "live",
    "концерт",
    "полный концерт",
    "полная версия",
    "полный альбом",
    "сборник",
    "плейлист",
    "микс",
    "стрим",
    "stream",
    "hour",
    "hours",
    "2 часа",
    "1 час",
    "90 min",
    "60 min",
    "120 min",
]

# “хорошие” слова — усиливают ранжирование
GOOD_WORDS = [
    "кавер",
    "cover",
    "acoustic",
    "акустика",
    "guitar",
    "гитара",
    "vocal",
    "вокал",
]

# Темы поиска (можно расширять, но лучше не раздувать)
# ВАЖНО: чтобы были русские — добавляем “кавер”, “на русском”, “русский кавер”
QUERY_THEMES = [
    "кавер песня",
    "кавер на русском",
    "русский кавер",
    "акустический кавер",
    "рок кавер",
    "метал кавер",
    "acoustic cover song",
    "rock cover song",
    "metal cover song",
]


# =========================
# HELPERS
# =========================

def _utc_now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[c_youtube] {now} {msg}", flush=True)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _looks_bad_title(title: str) -> bool:
    t = _norm(title)
    if not t:
        return True
    for w in STOP_WORDS:
        if w in t:
            return True
    return False


def _score_title(title: str, duration: Optional[int]) -> float:
    """
    Чем больше — тем лучше.
    Хотим: кавер/cover, акустика, коротко, без live/концертов.
    """
    t = _norm(title)
    score = 0.0

    # базовый буст за "кавер"/"cover"
    if "кавер" in t:
        score += 5.0
    if "cover" in t:
        score += 4.0

    # дополнительные хорошие слова
    for w in GOOD_WORDS:
        if w in t:
            score += 1.0

    # штраф за слишком длинное
    if duration is not None:
        if duration > MAX_DURATION_SEC:
            score -= 100.0
        else:
            # чуть предпочтём 2-4 минуты
            if 120 <= duration <= 300:
                score += 1.5
            elif 300 < duration <= MAX_DURATION_SEC:
                score += 0.5

        if duration < MIN_DURATION_SEC:
            score -= 10.0

    return score


def _extract_video_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    # yt-dlp обычно отдаёт id отдельно; если нет — попробуем вытащить из URL
    m = re.search(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})", s)
    return m.group(1) if m else s


def _ydl() -> yt_dlp.YoutubeDL:
    # Важно: не качаем, только мета.
    # nocheckcertificate иногда помогает в некоторых окружениях.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "ignoreerrors": True,
        # иногда полезно:
        "default_search": "ytsearch",
    }
    return yt_dlp.YoutubeDL(opts)


def _search(query: str, n: int) -> List[Dict[str, Any]]:
    """
    Возвращает список entries от yt-dlp.
    """
    q = (query or "").strip()
    if not q:
        return []

    target = f"ytsearch{max(1, n)}:{q}"
    with _ydl() as ydl:
        info = ydl.extract_info(target, download=False)

    entries = []
    if isinstance(info, dict):
        entries = info.get("entries") or []
    return [e for e in entries if isinstance(e, dict)]


def _is_dead_entry(e: Dict[str, Any]) -> bool:
    """
    Мёртвые/недоступные часто приходят без нормального url/id или с ошибкой.
    yt-dlp при ignoreerrors может вернуть None/пустое.
    """
    vid = (e.get("id") or "").strip()
    url = (e.get("webpage_url") or e.get("url") or "").strip()
    if not vid and not url:
        return True
    # иногда yt-dlp даёт availability
    availability = _norm(str(e.get("availability") or ""))
    if availability in {"private", "needs_auth", "subscriber_only"}:
        return True
    return False


# =========================
# PUBLIC API
# =========================

def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Возвращает items для main.py:
      {
        "feed": "c_youtube",
        "url": "https://www.youtube.com/watch?v=....",
        "video_id": "...",
        "title": "...",
        "source": "<theme>",
        "ts": <unix utc seconds>
      }
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    posted_video_ids = posted_video_ids or set()
    last_sent_by_source = last_sent_by_source or {}

    out: List[Dict[str, Any]] = []
    used_ids: Set[str] = set()

    themes = QUERY_THEMES[:MAX_QUERIES_PER_BATCH]

    dead_skipped = 0
    checked = 0

    for qi, theme in enumerate(themes, start=1):
        if len(out) >= limit:
            break

        _log(f"query={qi}/{len(themes)} theme='{theme}'")

        entries = _search(theme, PER_QUERY)
        _log(f"urls_found={len(entries)}")

        scored: List[Dict[str, Any]] = []

        for e in entries:
            if len(out) >= limit:
                break

            checked += 1

            if not e or _is_dead_entry(e):
                dead_skipped += 1
                continue

            title = (e.get("title") or "").strip()
            if _looks_bad_title(title):
                dead_skipped += 1
                _log(f"skip dead/filtered youtube: {e.get('webpage_url') or e.get('url')}")
                continue

            duration = e.get("duration")
            try:
                duration_i = int(duration) if duration is not None else None
            except Exception:
                duration_i = None

            if duration_i is not None:
                if duration_i > MAX_DURATION_SEC or duration_i < MIN_DURATION_SEC:
                    dead_skipped += 1
                    _log(f"skip dead/filtered youtube: {e.get('webpage_url') or e.get('url')}")
                    continue

            vid = (e.get("id") or "").strip()
            url = (e.get("webpage_url") or "").strip()
            if not url:
                # иногда url лежит в "url" (короткое), соберём нормальную ссылку
                cand = (e.get("url") or "").strip()
                vid2 = _extract_video_id(vid or cand)
                if vid2:
                    url = f"https://www.youtube.com/watch?v={vid2}"
                vid = vid2 or vid

            if not vid:
                dead_skipped += 1
                _log(f"skip dead/filtered youtube: {url or '(no-url)'}")
                continue

            if vid in posted_video_ids or vid in used_ids:
                continue

            s = _score_title(title, duration_i)
            scored.append(
                {
                    "video_id": vid,
                    "url": url,
                    "title": title,
                    "source": theme,
                    "ts": _utc_now_ts(),
                    "_score": s,
                }
            )

        # берём лучших из темы
        scored.sort(key=lambda x: float(x.get("_score", 0.0)), reverse=True)

        for it in scored:
            if len(out) >= limit:
                break

            vid = it["video_id"]
            if vid in posted_video_ids or vid in used_ids:
                continue

            used_ids.add(vid)
            out.append(
                {
                    "feed": "c_youtube",
                    "url": it["url"],
                    "video_id": it["video_id"],
                    "title": it.get("title", ""),
                    "source": it.get("source", ""),
                    "ts": it.get("ts", _utc_now_ts()),
                }
            )

    _log(f"returning={len(out)} limit={limit} checked={checked} dead_skipped={dead_skipped}")
    return out
