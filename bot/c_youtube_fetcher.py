from __future__ import annotations

import os
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import requests


YOUTUBE_WATCH = "https://www.youtube.com/watch?v="
YOUTUBE_SEARCH_RSS = "https://www.youtube.com/feeds/videos.xml?search_query="

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_SEARCH_QUERIES: List[str] = [
    "metal cover",
    "rock cover",
    "heavy metal cover",
    "hard rock cover",
    "guitar cover metal",
    "vocal cover metal",
    "кавер рок",
    "кавер металл",
    "рок кавер",
    "метал кавер",
    "metal cover русская песня",
    "rock cover русская песня",
    "кавер иностранная песня рок",
    "кавер иностранная песня металл",
    "rock cover pop song",
    "metal cover pop song",
]


@dataclass(frozen=True)
class CItem:
    video_id: str
    url: str
    title: str
    source: str


def _env_list(name: str) -> List[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    parts: List[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        t = chunk.strip()
        if t:
            parts.append(t)
    return parts


def _include_keywords() -> List[str]:
    return _env_list("C_INCLUDE_KEYWORDS") or [
        "cover", "кавер",
        "rock", "рок",
        "metal", "метал", "металл",
        "tribute", "version",
    ]


def _exclude_keywords() -> List[str]:
    return _env_list("C_EXCLUDE_KEYWORDS") or [
        "reaction", "реакц",
        "shorts",
        "стрим", "live stream",
        "podcast",
        "interview", "обзор",
    ]


def _looks_ok_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    exc = _exclude_keywords()
    if exc and any(k in t for k in exc):
        return False
    inc = _include_keywords()
    return bool(inc) and any(k in t for k in inc)


def _extract_entry(entry: ET.Element) -> Optional[Dict[str, str]]:
    title_el = entry.find("atom:title", _NS)
    title = (title_el.text or "").strip() if title_el is not None else ""

    vid_el = entry.find("yt:videoId", _NS)
    video_id = (vid_el.text or "").strip() if vid_el is not None else ""

    url = ""
    link_el = entry.find("atom:link[@rel='alternate']", _NS)
    if link_el is None:
        link_el = entry.find("atom:link", _NS)
    if link_el is not None:
        url = (link_el.attrib.get("href") or "").strip()

    if not video_id and url:
        m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
        if m:
            video_id = m.group(1)

    if not video_id:
        return None
    if not url:
        url = YOUTUBE_WATCH + video_id

    if not _looks_ok_title(title):
        return None

    return {"video_id": video_id, "url": url, "title": title}


def _fetch_search_rss(query: str, timeout: int = 20) -> List[Dict[str, str]]:
    q = query.strip().replace(" ", "+")
    url = YOUTUBE_SEARCH_RSS + q

    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    root = ET.fromstring(r.text)

    out: List[Dict[str, str]] = []
    for entry in root.findall("atom:entry", _NS):
        it = _extract_entry(entry)
        if it:
            out.append(it)
    return out


def get_batch(*, limit: int, posted_video_ids: Set[str]) -> List[Dict[str, str]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    tries = int(os.getenv("C_SEARCH_TRIES", "8"))
    tries = max(1, min(25, tries))

    queries = _env_list("C_SEARCH_QUERIES") or DEFAULT_SEARCH_QUERIES
    if not queries:
        return []

    picked = queries[:]
    random.shuffle(picked)
    picked = picked[:tries]

    seen_vid: Set[str] = set()
    out: List[Dict[str, str]] = []

    for q in picked:
        try:
            items = _fetch_search_rss(q)
        except Exception:
            continue

        for it in items:
            vid = it["video_id"]
            if vid in seen_vid:
                continue
            seen_vid.add(vid)

            if vid in posted_video_ids:
                continue

            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": it["url"],
                    "title": it["title"],
                    "src": q,
                }
            )
            if len(out) >= limit:
                return out

    return out
