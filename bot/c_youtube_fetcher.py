import time
import random
from yt_dlp import YoutubeDL

# максимум 10 минут
MAX_DURATION = 600

# cookies путь (Render persistent disk)
COOKIE_FILE = "/data/cookies.txt"

SEARCH_QUERIES = [
    "rock cover",
    "metal cover",
    "acoustic cover",
    "rock cover live",
    "metal cover live",
    "acoustic cover live",
    "кавер",
    "рок кавер",
    "метал кавер",
    "акустический кавер",
]


def _is_good_video(info):
    if not info:
        return False

    duration = info.get("duration")
    title = (info.get("title") or "").lower()

    if not duration:
        return False

    if duration > MAX_DURATION:
        return False

    bad_words = [
        "concert",
        "full concert",
        "full album",
        "playlist",
        "live stream",
        "stream",
        "mix",
        "compilation",
        "сборник",
        "концерт",
        "альбом",
        "плейлист",
    ]

    for w in bad_words:
        if w in title:
            return False

    return True


def _search(query, limit):
    ydl_opts = {
        "quiet": True,
        "cookiefile": COOKIE_FILE,
        "extract_flat": False,
        "skip_download": True,
    }

    results = []

    try:
        with YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(
                f"ytsearch{limit}:{query}",
                download=False
            )

            if not data or "entries" not in data:
                return []

            for entry in data["entries"]:
                if _is_good_video(entry):
                    results.append(entry)

    except Exception as e:
        print("[c_youtube] search error:", e)

    return results


def get_batch(limit=2, posted_video_ids=None, last_sent_by_source=None):
    if posted_video_ids is None:
        posted_video_ids = set()

    out = []
    used = set()

    for query in SEARCH_QUERIES:

        candidates = _search(query, 6)

        for info in candidates:

            vid = info.get("id")

            if not vid:
                continue

            if vid in posted_video_ids:
                continue

            if vid in used:
                continue

            used.add(vid)

            url = f"https://www.youtube.com/watch?v={vid}"

            out.append({
                "feed": "c_youtube",
                "url": url,
                "video_id": vid,
                "title": info.get("title"),
                "ts": int(time.time()),
            })

            if len(out) >= limit:
                return out

        time.sleep(1)

    return out
