 from __future__ import annotations

import os
import re
import time
from typing import Dict, Any, List, Optional, Set

from yt_dlp import YoutubeDL


# ------------------------
# Config
# ------------------------
DEBUG = (os.getenv("O_DEBUG", "0").strip() == "1")

# Сколько ссылок нужно вернуть (приходит из main.py)
# limit передаётся в get_batch()

# Сколько попыток поиска/проверки делаем, если первые результаты "мусор/мертвые"
MAX_SEARCH_CALLS = int(os.getenv("C_YT_MAX_SEARCH_CALLS", "6"))        # было много — держим умеренно
SEARCH_RESULTS_PER_CALL = int(os.getenv("C_YT_SEARCH_RESULTS", "8"))   # не раздуваем
MIN_INTERVAL_SEC = float(os.getenv("C_YT_MIN_INTERVAL_SEC", "0.8"))

# Фильтр по длительности (сек)
MIN_DURATION_SEC = int(os.getenv("C_YT_MIN_DURATION_SEC", "90"))      # минимум 1:30
MAX_DURATION_SEC = int(os.getenv("C_YT_MAX_DURATION_SEC", "900"))     # максимум 15:00 (чтобы не концерты)

# Cookies: по умолчанию ищем на persistent disk
# ВАЖНО: положи cookies.txt именно в /data/cookies.txt на Render
COOKIES_FILE = (os.getenv("YT_COOKIES_FILE") or "/data/cookies.txt").strip()

# Если куки нет, всё равно попробуем, но YouTube может резать
USE_COOKIES = os.path.exists(COOKIES_FILE)

_last_call_ts = 0.0


def _log(msg: str) -> None:
    if DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[c_youtube] {now} {msg}", flush=True)


def _sleep_rate_limit() -> None:
    global _last_call_ts
    now = time.time()
    dt = now - _last_call_ts
    if dt < MIN_INTERVAL_SEC:
        time.sleep(MIN_INTERVAL_SEC - dt)
    _last_call_ts = time.time()


def _clean(s: str) -> str:
    return (s or "").strip()


def _is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return ("youtube.com/watch" in u) or ("youtu.be/" in u)


def _extract_video_id(url: str) -> str:
    if not url:
        return ""
    # youtu.be/<id>
    m = re.search(r"youtu\.be/([A-Za-z0-9_\-]{6,})", url)
    if m:
        return m.group(1)
    # youtube.com/watch?v=<id>
    m = re.search(r"[?&]v=([A-Za-z0-9_\-]{6,})", url)
    if m:
        return m.group(1)
    return ""


def _bad_title(title: str) -> bool:
    t = (title or "").lower()

    # режем концерты/плейлисты/сборники/альбомы/миксы
    bad_words = [
        "concert", "концерт", "live session", "лайв", "живой концерт",
        "full concert", "полный концерт", "полностью", "full show",
        "playlist", "плейлист", "mix", "микс",
        "album", "альбом", "сборник",
        "compilation", "сборка",
        "час", "1 час", "2 час", "3 час", "hours", "hour",
        "full", "полная версия", "полное выступление",
    ]

    for w in bad_words:
        if w in t:
            return True

    return False


def _looks_like_cover(title: str) -> bool:
    t = (title or "").lower()
    # хотим каверы: cover / кавер / tribute / guitar cover etc
    good = ["cover", "кавер", "tribute", "guitar cover", "metal cover", "rock cover", "acoustic cover"]
    return any(g in t for g in good)


def _yt_opts() -> Dict[str, Any]:
    """
    Настройки yt-dlp:
    - android client сильно снижает блокировки
    - cookiesfile если есть
    """
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "noplaylist": True,
        "extract_flat": False,   # нам нужен duration/title
        "skip_download": True,
        "retries": 1,
        "socket_timeout": 20,
        "http_chunk_size": 0,

        # КЛЮЧЕВОЕ: клиент android
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    if USE_COOKIES:
        opts["cookiefile"] = COOKIES_FILE

    return opts


def _search_yt(query: str, max_results: int) -> List[Dict[str, Any]]:
    """
    Возвращает entries от ytsearch.
    """
    q = f"ytsearch{max_results}:{query}"
    _sleep_rate_limit()
    with YoutubeDL(_yt_opts()) as ydl:
        info = ydl.extract_info(q, download=False)
    entries = info.get("entries") or []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        out.append(e)
    return out


def _validate_video(url: str) -> Optional[Dict[str, Any]]:
    """
    Проверяем что видео реально парсится и это не "только картинки".
    """
    if not url:
        return None

    _sleep_rate_limit()
    try:
        with YoutubeDL(_yt_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        _log(f"validate fail url={url} err={e}")
        return None

    if not isinstance(info, dict):
        return None

    # Бывает, что YouTube отдаёт только thumbnails — тогда duration отсутствует
    title = _clean(info.get("title") or "")
    duration = info.get("duration")
    if duration is None:
        _log(f"validate got no duration (likely blocked) url={url} title={title[:60]}")
        return None

    try:
        dur = int(duration)
    except Exception:
        return None

    # фильтры
    if dur < MIN_DURATION_SEC or dur > MAX_DURATION_SEC:
        return None

    if not title or _bad_title(title):
        return None

    # лёгкая подсказка: хотим каверность
    # не делаем это жёстким, но предпочтём такие
    info["_is_coverish"] = _looks_like_cover(title)

    return info


def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Возвращает список айтемов формата:
      {
        "feed": "c_youtube",
        "url": "...",
        "video_id": "...",
        "source": "yt:<video_id>",
        "title": "...",
        "ts": <unix_ts>
      }

    ВАЖНО: тут НЕТ OpenAI. Только yt-dlp поиск + фильтры.
    """

    limit = int(limit or 0)
    if limit <= 0:
        return []

    used_ids: Set[str] = set(posted_video_ids or set())

    # Темы: добавили русское слово "кавер" + "на русском"
    # Плюс минус-слова чтобы не вытаскивало концерты/плейлисты.
    themes = [
        "rock cover song",
        "acoustic cover",
        "metal cover",
        "kaver na russkom",
        "кавер на русском",
        "русский кавер",
        "рок кавер",
        "metal cover na russkom",
    ]

    negatives = "-concert -концерт -live -playlist -плейлист -album -альбом -mix -микс -full -час"

    out: List[Dict[str, Any]] = []
    checked = 0
    dead_skipped = 0
    calls = 0

    # Сильно не раздуваем: ищем, валидируем, пока не соберём limit
    for idx, theme in enumerate(themes, start=1):
        if len(out) >= limit:
            break
        if calls >= MAX_SEARCH_CALLS:
            break

        query = f"{theme} {negatives}".strip()
        calls += 1

        try:
            entries = _search_yt(query, SEARCH_RESULTS_PER_CALL)
        except Exception as e:
            _log(f"search error theme={theme!r} err={e}")
            continue

        _log(f"query={idx}/{len(themes)} theme={theme!r} urls_found={len(entries)}")

        for e in entries:
            if len(out) >= limit:
                break

            # URL
            url = _clean(e.get("webpage_url") or e.get("url") or "")
            if not url:
                # иногда entries только с id
                vid = _clean(e.get("id") or "")
                if vid:
                    url = f"https://www.youtube.com/watch?v={vid}"

            if not _is_youtube_url(url):
                continue

            vid = _extract_video_id(url)
            if not vid:
                continue

            if vid in used_ids:
                continue

            checked += 1

            info = _validate_video(url)
            if info is None:
                dead_skipped += 1
                _log(f"skip dead/filtered youtube: {url}")
                continue

            title = _clean(info.get("title") or "")
            dur = int(info.get("duration") or 0)

            # мягко приоритетим каверные тайтлы
            is_coverish = bool(info.get("_is_coverish"))
            item = {
                "feed": "c_youtube",
                "url": url,
                "video_id": vid,
                "source": f"yt:{vid}",
                "title": title,
                "duration_sec": dur,
                "ts": int(time.time()),
                "_prio": 1 if is_coverish else 0,
            }

            out.append(item)
            used_ids.add(vid)

        # если уже что-то набрали — не надо выжигать все темы
        if len(out) >= limit:
            break

    # сортировка: сначала более “coverish”
    out.sort(key=lambda x: (x.get("_prio", 0), x.get("ts", 0)), reverse=True)

    # финально режем
    out = out[:limit]

    # убрать служебное
    for it in out:
        it.pop("_prio", None)

    _log(f"returning={len(out)} limit={limit} calls={calls} checked={checked} dead_skipped={dead_skipped} cookies={USE_COOKIES}")
    return out
