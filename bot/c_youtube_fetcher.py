from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import yt_dlp


# ===== settings =====
COOKIES_PATH = os.getenv("YT_COOKIES_PATH", "/data/cookies.txt").strip()
MAX_QUERIES = int(os.getenv("YT_MAX_QUERIES", "6"))          # не долбим 20+ запросов
SEARCH_PER_QUERY = int(os.getenv("YT_SEARCH_PER_QUERY", "6")) # кандидатов мало
MAX_CHECK_PER_QUERY = int(os.getenv("YT_MAX_CHECK_PER_QUERY", "4"))  # сколько видео реально "проверяем" на метаданные

# хотим "песню", а не 2 часа концерта
MAX_DURATION_SEC = int(os.getenv("YT_MAX_DURATION_SEC", str(12 * 60)))  # 12 минут
MIN_DURATION_SEC = int(os.getenv("YT_MIN_DURATION_SEC", "80"))          # 1:20

# стоп-слова в заголовке
BAD_TITLE_RE = re.compile(
    r"(?i)\b("
    r"concert|концерт|full|полный|playlist|плейлист|album|альбом|mix|сборник|compilation|"
    r"live session|session|стрим|stream|"
    r"\b1\s*hour\b|\b2\s*hour\b|\b3\s*hour\b|час|часа|hours|"
    r"full show|full concert|"
    r"караоке|lyrics|текст\b|lyrics video|"
    r")\b"
)

# "мягкие" подсказки что это именно кавер/песня
GOOD_HINT_RE = re.compile(r"(?i)\b(cover|кавер|перепел|перепевка|tribute|acoustic|акустик)\b")

# чистим ID
YT_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})")


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


def _extract_video_id(url: str) -> str:
    m = YT_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _has_cookies() -> bool:
    return bool(COOKIES_PATH) and os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0


def _ydl_base_opts() -> dict:
    # ВАЖНО:
    # - НЕ ставим format=..., иначе можешь снова словить "Requested format is not available"
    # - download=False + skip_download=True
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": True,   # на этапе поиска берём "плоские" результаты
        "default_search": "ytsearch",
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 1,
    }
    if _has_cookies():
        opts["cookiefile"] = COOKIES_PATH
    return opts


def _ydl_full_opts() -> dict:
    # Для получения duration/title по конкретному видео
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 1,
    }
    if _has_cookies():
        opts["cookiefile"] = COOKIES_PATH
    return opts


def _build_queries() -> List[str]:
    # ВАЖНО: формулировки которые чаще дают "песню", а не концерт
    # + отрицательные слова прямо в запросе
    neg = "-концерт -concert -live -full -playlist -плейлист -альбом -album -mix -сборник -session -стрим -stream"
    return [
        f"рок кавер на русском {neg}",
        f"метал кавер на русском {neg}",
        f"кавер песня на русском {neg}",
        f"acoustic cover russian {neg}",
        f"rock cover song {neg}",
        f"metal cover guitar vocal {neg}",
    ]


def _is_bad_title(title: str) -> bool:
    if not title:
        return True
    return bool(BAD_TITLE_RE.search(title))


def _is_good_by_hint(title: str) -> bool:
    return bool(GOOD_HINT_RE.search(title or ""))


def _duration_ok(dur: Optional[int]) -> bool:
    if dur is None:
        return True  # если длительность не пришла — не валим сразу
    try:
        dur = int(dur)
    except Exception:
        return True
    return MIN_DURATION_SEC <= dur <= MAX_DURATION_SEC


def _normalize_url(url: str, vid: str) -> str:
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url


def _search_candidates(query: str, n: int) -> List[dict]:
    # Возвращает flat-результаты поиска
    q = f"ytsearch{n}:{query}"
    with yt_dlp.YoutubeDL(_ydl_base_opts()) as ydl:
        try:
            data = ydl.extract_info(q, download=False)
        except Exception as e:
            log(f"search error query='{query}': {e}")
            return []

    entries = (data or {}).get("entries") or []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        out.append(e)
    return out


def _fetch_full_info(url: str) -> Optional[dict]:
    with yt_dlp.YoutubeDL(_ydl_full_opts()) as ydl:
        try:
            # extract_flat=False тут (по умолчанию) -> получим duration/title нормально
            return ydl.extract_info(url, download=False)
        except Exception as e:
            # частая проблема: "Sign in to confirm you're not a bot"
            log(f"info error url={url}: {e}")
            return None


def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict]:
    """
    Возвращает список айтемов формата, который ждёт main.py:
      {
        "feed": "c_youtube",
        "url": "...",
        "video_id": "...",
        "source": "yt:<id>",
        "title": "...",
        "ts": <unix>,
      }
    """
    limit = int(limit or 0)
    if limit <= 0:
        return []

    if _has_cookies():
        log(f"cookies: OK ({COOKIES_PATH})")
    else:
        log(f"cookies: MISSING (expected at {COOKIES_PATH})")

    queries = _build_queries()[:MAX_QUERIES]
    used_ids: Set[str] = set(posted_video_ids or set())
    out: List[Dict] = []
    dead_skipped = 0
    checked_total = 0

    for qi, query in enumerate(queries, start=1):
        if len(out) >= limit:
            break

        log(f"query={qi}/{len(queries)} theme='{query}'")
        candidates = _search_candidates(query, SEARCH_PER_QUERY)
        log(f"urls_found={len(candidates)}")

        checked_this_query = 0

        for c in candidates:
            if len(out) >= limit:
                break
            if checked_this_query >= MAX_CHECK_PER_QUERY:
                break

            # кандидаты из flat обычно имеют id/url/title
            vid = (c.get("id") or "").strip()
            title = (c.get("title") or "").strip()
            url = (c.get("url") or c.get("webpage_url") or "").strip()

            if not vid and url:
                vid = _extract_video_id(url)
            if not url and vid:
                url = f"https://www.youtube.com/watch?v={vid}"

            if not vid or not url:
                continue
            if vid in used_ids:
                continue
            if _is_bad_title(title):
                dead_skipped += 1
                continue

            # проверяем "полные" метаданные (duration/title)
            checked_this_query += 1
            checked_total += 1

            info = _fetch_full_info(url)
            if not info:
                dead_skipped += 1
                continue

            # иногда yt_dlp возвращает redirect/shorts — нормализуем
            info_title = (info.get("title") or title or "").strip()
            info_id = (info.get("id") or vid or "").strip()
            dur = info.get("duration")

            # фильтр по длительности и стоп-словам
            if _is_bad_title(info_title):
                dead_skipped += 1
                continue
            if not _duration_ok(dur):
                dead_skipped += 1
                continue

            # мягко предпочитаем каверы: если заголовок вообще не похож — пропускаем
            # (иначе улетает “Аквариум — Истребитель” оригинал, концерты, и т.д.)
            if not _is_good_by_hint(info_title):
                dead_skipped += 1
                continue

            info_url = (info.get("webpage_url") or url or "").strip()
            info_url = _normalize_url(info_url, info_id)

            item = YTItem(
                url=info_url,
                video_id=info_id,
                title=info_title,
                source=f"yt:{info_id}",
                ts=int(time.time()),
            )

            out.append(
                {
                    "feed": "c_youtube",
                    "url": item.url,
                    "video_id": item.video_id,
                    "source": item.source,
                    "title": item.title,
                    "ts": item.ts,
                }
            )
            used_ids.add(item.video_id)

        log(f"progress: got={len(out)}/{limit} checked_total={checked_total} skipped={dead_skipped}")

    log(f"returning={len(out)} limit={limit} checked_total={checked_total} dead_skipped={dead_skipped}")
    return out
