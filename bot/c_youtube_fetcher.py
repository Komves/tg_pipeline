from __future__ import annotations

import os
import random
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set

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

# запросы только про каверы + рок/метал
SEARCH_QUERIES: List[str] = [
    "metal cover",
    "rock cover",
    "heavy metal cover",
    "hard rock cover",
    "metal cover russian song",
    "rock cover russian song",
    "кавер рок",
    "кавер металл",
    "рок кавер",
    "метал кавер",
    "металл кавер",
    "кавер песня рок",
    "кавер песня металл",
]

EXCLUDE = [
    "reaction",
    "реакц",
    "shorts",
    "стрим",
    "live stream",
    "podcast",
    "interview",
    "tutorial",
    "lesson",
    "обзор",
    "разбор",
]

# нужно: cover/кавер + rock/metal/рок/метал
_RE_NEED = re.compile(r"(cover|кавер)", re.IGNORECASE)
_RE_GENRE = re.compile(r"(rock|metal|рок|метал|металл)", re.IGNORECASE)


def _debug(msg: str) -> None:
    if (os.getenv("C_DEBUG", "0") or "").strip() in ("1", "true", "yes", "on"):
        print(f"[c_youtube] {msg}", flush=True)


def _ok_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False

    low = t.lower()
    for bad in EXCLUDE:
        if bad in low:
            return False

    if not _RE_NEED.search(t):
        return False

    if not _RE_GENRE.search(t):
        return False

    return True


def _parse_entry(entry: ET.Element) -> Optional[Dict[str, str]]:
    title_el = entry.find("atom:title", _NS)
    vid_el = entry.find("yt:videoId", _NS)

    if title_el is None or vid_el is None:
        return None

    title = (title_el.text or "").strip()
    vid = (vid_el.text or "").strip()
    if not vid:
        return None

    if not _ok_title(title):
        return None

    return {
        "feed": "c_youtube",
        "item_id": vid,
        "video_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title,
        "src": "youtube_search",
    }


def _fetch(query: str) -> List[Dict[str, str]]:
    url = YOUTUBE_SEARCH_RSS + query.replace(" ", "+")
    _debug(f"fetch url={url}")

    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    _debug(f"status={r.status_code} bytes={len(r.content)}")

    r.raise_for_status()

    root = ET.fromstring(r.text)

    entries = root.findall("atom:entry", _NS)
    _debug(f"entries={len(entries)}")

    out: List[Dict[str, str]] = []
    for e in entries:
        it = _parse_entry(e)
        if it:
            out.append(it)

    _debug(f"matched_after_filter={len(out)}")
    return out


def get_batch(*, limit: int, posted_video_ids: Set[str]) -> List[Dict[str, str]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    tries = int(os.getenv("C_SEARCH_TRIES", "8"))
    tries = max(1, min(25, tries))

    queries = SEARCH_QUERIES[:]
    random.shuffle(queries)
    queries = queries[:tries]

    _debug(f"limit={limit} tries={tries} posted_count={len(posted_video_ids)} queries={queries}")

    out: List[Dict[str, str]] = []
    seen: Set[str] = set()

    total_entries = 0
    total_matched = 0
    total_dedup = 0
    total_posted_skip = 0

    for q in queries:
        try:
            items = _fetch(q)
        except Exception as e:
            _debug(f"fetch_error query={q} err={e}")
            continue

        total_matched += len(items)

        for it in items:
            vid = it["video_id"]

            if vid in seen:
                total_dedup += 1
                continue
            seen.add(vid)

            if vid in posted_video_ids:
                total_posted_skip += 1
                continue

            out.append(it)
            if len(out) >= limit:
                _debug(
                    f"done: out={len(out)} matched={total_matched} dedup={total_dedup} posted_skip={total_posted_skip}"
                )
                return out

    _debug(f"done: out={len(out)} matched={total_matched} dedup={total_dedup} posted_skip={total_posted_skip}")
    return out
