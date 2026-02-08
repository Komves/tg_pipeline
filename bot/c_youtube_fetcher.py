from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import requests


YOUTUBE_WATCH = "https://www.youtube.com/watch?v="

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


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
        "cover",
        "кавер",
        "rock",
        "рок",
        "metal",
        "метал",
        "металл",
        "tribute",
        "version",
    ]


def _exclude_keywords() -> List[str]:
    return _env_list("C_EXCLUDE_KEYWORDS") or [
        "reaction",
        "реакц",
        "shorts",
        "стрим",
        "live stream",
        "podcast",
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


def _extract_entry(entry: ET.Element) -> Optional[CItem]:
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

    return CItem(video_id=video_id, url=url, title=title, source="")


def _fetch_feed(url: str, timeout: int = 15) -> List[CItem]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.text)

    out: List[CItem] = []
    for entry in root.findall("atom:entry", _NS):
        it = _extract_entry(entry)
        if it:
            out.append(it)
    return out


def get_batch(*, limit: int, posted_video_ids: Set[str]) -> List[Dict[str, str]]:
    """
    Возвращает items для main.py:
      {"feed":"c_youtube","item_id":video_id,"video_id":...,"url":...,"title":...,"src":...}
    Настройка источников через env:
      C_RSS_FEEDS = RSS-URL (через запятую или перенос строки)
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    feeds = _env_list("C_RSS_FEEDS")
    if not feeds:
        return []

    candidates: List[CItem] = []
    for f in feeds:
        try:
            items = _fetch_feed(f)
            for it in items:
                candidates.append(CItem(it.video_id, it.url, it.title, source=f))
        except Exception:
            continue

    seen: Set[str] = set()
    out: List[Dict[str, str]] = []
    for it in candidates:
        if it.video_id in seen:
            continue
        seen.add(it.video_id)

        if it.video_id in posted_video_ids:
            continue

        out.append(
            {
                "feed": "c_youtube",
                "item_id": it.video_id,
                "video_id": it.video_id,
                "url": it.url,
                "title": it.title,
                "src": it.source,
            }
        )
        if len(out) >= limit:
            break

    return out
