from __future__ import annotations

import os
import re
import time
import random
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Set

# =========================
# CONFIG
# =========================

C_MAX_DURATION_SEC = int(os.getenv("C_MAX_DURATION_SEC", "540"))
C_MIN_DURATION_SEC = int(os.getenv("C_MIN_DURATION_SEC", "75"))

C_MAX_QUERIES_PER_RUN = int(os.getenv("C_MAX_QUERIES_PER_RUN", "6"))
C_REQUIRE_RU = True

# =========================
# LOG
# =========================

def _log(msg: str):
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[c_youtube] {now} {msg}", flush=True)


# =========================
# FILTERS
# =========================

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

BAD_TITLE_RE = re.compile(
    r"(concert|live|session|playlist|album|mix|compilation|set list|full show|full set)",
    re.IGNORECASE,
)

GOOD_RE = re.compile(r"(cover|кавер)", re.IGNORECASE)


def _themes():

    ru = [
        "кавер на русском",
        "rock cover на русском",
        "metal cover на русском",
        "acoustic cover на русском",
        "guitar cover на русском",
    ]

    en = [
        "rock cover",
        "metal cover",
        "acoustic cover",
        "guitar cover",
        "female vocal cover",
    ]

    out = ru + en
    random.shuffle(out)

    return out


# =========================
# RSS SEARCH
# =========================

def _rss_search(query: str):

    url = "https://www.youtube.com/feeds/videos.xml"

    try:

        r = requests.get(
            url,
            params={"search_query": query},
            timeout=15,
        )

        if r.status_code != 200:
            return []

        root = ET.fromstring(r.text)

        ns = {
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "atom": "http://www.w3.org/2005/Atom",
        }

        out = []

        for entry in root.findall("atom:entry", ns):

            vid = entry.find("yt:videoId", ns)
            title = entry.find("atom:title", ns)

            if vid is None:
                continue

            vid = vid.text.strip()

            t = title.text.strip() if title is not None else ""

            out.append(
                {
                    "video_id": vid,
                    "title": t,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }
            )

        return out

    except Exception as e:

        _log(f"rss error: {e}")

        return []


# =========================
# FILTER ENTRY
# =========================

def _ok(e):

    title = e["title"]

    if BAD_TITLE_RE.search(title):
        return False

    if not GOOD_RE.search(title):
        return False

    if C_REQUIRE_RU and not CYRILLIC_RE.search(title):
        return False

    return True


# =========================
# MAIN
# =========================

def get_batch(
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
):

    themes = _themes()

    picked = []

    for i, theme in enumerate(themes[:C_MAX_QUERIES_PER_RUN]):

        _log(f"query={i+1} theme='{theme}'")

        items = _rss_search(theme)

        _log(f"urls_found={len(items)}")

        for e in items:

            vid = e["video_id"]

            if vid in posted_video_ids:
                continue

            if not _ok(e):
                continue

            picked.append(
                {
                    "feed": "c_youtube",
                    "url": e["url"],
                    "video_id": vid,
                    "title": e["title"],
                    "source": f"yt:{vid}",
                    "ts": int(time.time()),
                }
            )

            posted_video_ids.add(vid)

            if len(picked) >= limit:

                _log(f"returning={len(picked)}")

                return picked

    _log(f"returning={len(picked)}")

    return picked
