import time
from yt_dlp import YoutubeDL

# максимум 10 минут
MAX_DURATION = 600

# cookies должны лежать на персистент диске Render
COOKIE_FILE = "/data/cookies.txt"

# сколько кандидатов максимум проверяем глубоко (чтоб не жрало ресурсы)
MAX_DEEP_CHECKS_PER_QUERY = 6

# сколько роликов просим у поиска на запрос (плоско)
SEARCH_LIMIT_PER_QUERY = 12

# задержка между запросами
SLEEP_BETWEEN_QUERIES_SEC = 0.6

SEARCH_QUERIES = [
    # EN
    "rock cover song",
    "metal cover song",
    "acoustic cover song",
    # RU
    "кавер песня",
    "рок кавер песня",
    "метал кавер песня",
    "акустический кавер песня",
    # дополнительные вариации
    "кавер на русском",
    "рок кавер на русском",
    "метал кавер на русском",
]

BAD_WORDS = [
    "concert", "full concert", "live stream", "stream", "playlist", "full album",
    "mix", "compilation", "сборник", "концерт", "альбом", "плейлист", "стрим"
]


def _log(msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


def _is_bad_title(title: str) -> bool:
    t = (title or "").lower()
    for w in BAD_WORDS:
        if w in t:
            return True
    return False


def _flat_search(query: str, limit: int):
    """
    Плоский поиск: достаем только ids/titles, без форматов.
    Это резко снижает шанс упасть на 'requested format not available'.
    """
    ydl_opts = {
        "quiet": True,
        "cookiefile": COOKIE_FILE,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
        "nocheckcertificate": True,
        # важное: меньше триггеров на защиту
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = (data or {}).get("entries") or []
            out = []
            for e in entries:
                if not e:
                    continue
                vid = e.get("id") or e.get("url")
                title = e.get("title") or ""
                if not vid:
                    continue
                if _is_bad_title(title):
                    continue
                out.append({"id": vid, "title": title})
            return out
    except Exception as e:
        _log(f"flat search error: {e}")
        return []


def _deep_get_info(video_id: str):
    """
    Глубокая проверка: получаем duration.
    Здесь и появляются ошибки форматов/защиты — поэтому ловим всё и пропускаем.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        "quiet": True,
        "cookiefile": COOKIE_FILE,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,

        # КРИТИЧНО: не требуем конкретный формат
        "format": "best",

        # КРИТИЧНО: не падать, если у ролика нет форматов (или YouTube не отдал)
        "ignore_no_formats_error": True,
        "ignoreerrors": True,

        # часто помогает обходить часть "challenge solving failed"
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        _log(f"deep info error for {url}: {e}")
        return None


def get_batch(limit=2, posted_video_ids=None, last_sent_by_source=None):
    posted_video_ids = posted_video_ids or set()
    out = []
    used = set()

    for qi, query in enumerate(SEARCH_QUERIES, start=1):
        _log(f"query={qi}/{len(SEARCH_QUERIES)} theme='{query}'")

        flat = _flat_search(query, SEARCH_LIMIT_PER_QUERY)
        _log(f"urls_found={len(flat)}")

        deep_checked = 0

        for cand in flat:
            vid = (cand.get("id") or "").strip()
            title = (cand.get("title") or "").strip()

            if not vid:
                continue
            if vid in posted_video_ids or vid in used:
                continue
            if _is_bad_title(title):
                continue

            used.add(vid)

            # ограничиваем "глубокие" проверки, чтобы не грузить и не упираться в защиту
            if deep_checked >= MAX_DEEP_CHECKS_PER_QUERY:
                break

            info = _deep_get_info(vid)
            deep_checked += 1

            if not info:
                _log(f"skip dead/filtered youtube: https://www.youtube.com/watch?v={vid}")
                continue

            duration = info.get("duration")
            real_title = (info.get("title") or title).strip()

            if not duration:
                _log(f"skip no-duration youtube: https://www.youtube.com/watch?v={vid}")
                continue

            if duration > MAX_DURATION:
                _log(f"skip too-long ({duration}s) youtube: https://www.youtube.com/watch?v={vid}")
                continue

            if _is_bad_title(real_title):
                _log(f"skip bad-title youtube: https://www.youtube.com/watch?v={vid}")
                continue

            out.append({
                "feed": "c_youtube",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "video_id": vid,
                "title": real_title,
                "source": f"yt:{vid}",
                "ts": int(time.time()),
            })

            if len(out) >= limit:
                _log(f"returning={len(out)} limit={limit} checked={deep_checked}")
                return out

        _log(f"after query checked={deep_checked} collected={len(out)}")
        time.sleep(SLEEP_BETWEEN_QUERIES_SEC)

        if len(out) >= limit:
            break

    _log(f"returning={len(out)} limit={limit}")
    return out
