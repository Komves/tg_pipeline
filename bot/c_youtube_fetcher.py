from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Set

import requests


YOUTUBE_SEARCH_RSS = "https://www.youtube.com/feeds/videos.xml?search_query="

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ЖЕСТКО cover-ориентированные поисковые запросы
SEARCH_QUERIES = [

    # English
    "metal cover song",
    "rock cover song",
    "heavy metal cover",
    "hard rock cover",
    "metal cover popular song",
    "rock cover popular song",
    "metal cover pop song",
    "rock cover pop song",

    # Russian
    "метал кавер",
    "рок кавер",
    "кавер рок песня",
    "кавер метал песня",

    # mixed
    "female metal cover",
    "male metal cover",
    "guitar metal cover",
    "vocal metal cover",

]


EXCLUDE = [
    "reaction",
    "shorts",
    "podcast",
    "interview",
    "lesson",
    "tutorial",
    "cover tutorial",
]


def _ok_title(title: str) -> bool:

    t = title.lower()

    if "cover" not in t and "кавер" not in t:
        return False

    for bad in EXCLUDE:
        if bad in t:
            return False

    return True


def _parse_entry(entry) -> Dict | None:

    title_el = entry.find("atom:title", _NS)
    vid_el = entry.find("yt:videoId", _NS)

    if title_el is None or vid_el is None:
        return None

    title = title_el.text or ""
    vid = vid_el.text or ""

    if not vid:
        return None

    if not _ok_title(title):
        return None

    return {
        "feed": "c_youtube",
        "item_id": vid,
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title.strip(),
        "src": "youtube_search",
    }


def _fetch(query: str) -> List[Dict]:

    url = YOUTUBE_SEARCH_RSS + query.replace(" ", "+")

    r = requests.get(
        url,
        headers={"User-Agent": UA},
        timeout=20,
    )

    root = ET.fromstring(r.text)

    out = []

    for entry in root.findall("atom:entry", _NS):

        it = _parse_entry(entry)

        if it:
            out.append(it)

    return out


def get_batch(*, limit: int, posted_video_ids: Set[str]) -> List[Dict]:

    queries = SEARCH_QUERIES[:]

    random.shuffle(queries)

    out = []
    seen = set()

    for q in queries:

        try:
            items = _fetch(q)
        except Exception:
            continue

        for it in items:

            vid = it["video_id"]

            if vid in seen:
                continue

            if vid in posted_video_ids:
                continue

            seen.add(vid)
            out.append(it)

            if len(out) >= limit:
                return out

    return out
