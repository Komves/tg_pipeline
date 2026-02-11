import time
import random
from yt_dlp import YoutubeDL

MAX_DURATION = 600  # максимум 10 минут


SEARCH_QUERIES = [
    "rock cover",
    "acoustic cover",
    "metal cover",
    "cover song",
    "кавер",
    "кавер песня",
    "рок кавер",
    "акустический кавер",
]


def log(msg):
    print(f"[c_youtube] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _search(query, limit):
    ydl = YoutubeDL({
        "quiet": True,
        "nocheckcertificate": True,
        "cookiefile": "/data/cookies.txt",
        "extract_flat": True,
        "skip_download": True,
    })

    try:
        info = ydl.extract_info(
            f"ytsearch{limit}:{query}",
            download=False
        )

        if not info or "entries" not in info:
            return []

        return info["entries"]

    except Exception as e:
        log(f"search error: {e}")
        return []


def _get_video_info(url):
    ydl = YoutubeDL({
        "quiet": True,
        "nocheckcertificate": True,
        "cookiefile": "/data/cookies.txt",
    })

    try:
        return ydl.extract_info(url, download=False)
    except Exception as e:
        log(f"info error: {e}")
        return None


def _is_valid_video(info):

    if not info:
        return False

    duration = info.get("duration")
    if not duration:
        return False

    if duration > MAX_DURATION:
        return False

    title = (info.get("title") or "").lower()

    bad_words = [
        "full album",
        "full concert",
        "concert",
        "playlist",
        "mix",
        "live stream",
    ]

    for word in bad_words:
        if word in title:
            return False

    return True


def get_batch(limit=2, posted_video_ids=set(), last_sent_by_source=None):

    log("starting search")

    out = []
    used_ids = set()

    queries = SEARCH_QUERIES.copy()
    random.shuffle(queries)

    for query in queries:

        if len(out) >= limit:
            break

        log(f"query={query}")

        results = _search(query, 5)

        for entry in results:

            if len(out) >= limit:
                break

            vid = entry.get("id")
            if not vid:
                continue

            if vid in posted_video_ids or vid in used_ids:
                continue

            url = f"https://www.youtube.com/watch?v={vid}"

            info = _get_video_info(url)

            if not _is_valid_video(info):
                log(f"skip dead/invalid youtube: {url}")
                continue

            used_ids.add(vid)

            out.append({
                "feed": "c_youtube",
                "url": url,
                "video_id": vid,
                "title": info.get("title"),
                "ts": int(time.time()),
            })

            log(f"added: {url}")

    log(f"returning={len(out)}")

    return out
