from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


DATA_DIR = Path("/data")
RAW_DIR = DATA_DIR / "raw"
FEEDBACK_TSV = DATA_DIR / "a_feedback_master.tsv"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

CAT_A_VIDEO = "A_VIDEO"

RECENCY_K = 2.0
RECENCY_TAU_H = 12.0

MAX_AGE_HOURS = 72


@dataclass
class RankedItem:
    item_id: str
    abs_path: str
    score: float
    tg_date_iso: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None


def read_meta_date(path: Path) -> Optional[datetime]:
    meta = Path(str(path) + ".meta.json")
    if not meta.exists():
        return None
    try:
        j = json.loads(meta.read_text(encoding="utf-8"))
        return parse_iso(j.get("tg_date"))
    except:
        return None


def iter_video_files():
    for p in RAW_DIR.rglob("*"):
        if p.suffix.lower() in VIDEO_EXT:
            yield p


def load_posted(user_id: int, feed: str) -> Set[str]:
    if not POSTED_TSV.exists():
        return set()
    out = set()
    with POSTED_TSV.open("r", encoding="utf-8") as f:
        r = csv.reader(f, delimiter="\t")
        for row in r:
            if len(row) < 4:
                continue
            _, u, item, ffeed = row
            if str(u) == str(user_id) and ffeed == feed:
                out.add(item)
    return out


def recency_score(age_h: float) -> float:
    return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)


def rank_top_n(user_id: int, category: str, n: int, *, feed="feed_a_video") -> List[RankedItem]:

    now = now_utc()
    posted = load_posted(user_id, feed)

    items: List[RankedItem] = []

    for p in iter_video_files():

        item_id = p.relative_to(RAW_DIR).as_posix()

        if item_id in posted:
            continue

        tg_dt = read_meta_date(p)
        if not tg_dt:
            continue

        age_h = (now - tg_dt).total_seconds() / 3600

        if age_h > MAX_AGE_HOURS:
            continue

        score = recency_score(age_h)

        items.append(
            RankedItem(
                item_id=item_id,
                abs_path=str(p),
                score=score,
                tg_date_iso=tg_dt.isoformat(),
            )
        )

    items.sort(key=lambda x: x.score, reverse=True)

    return items[:n]
