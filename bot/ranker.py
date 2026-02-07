from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set


DATA_DIR = Path("/data")
RAW_DIR = DATA_DIR / "raw"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"

# ТОЛЬКО мем-форматы
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

MAX_AGE_HOURS = 72.0

RECENCY_K = 2.0
RECENCY_TAU_H = 24.0


@dataclass(frozen=True)
class MemeItem:
    item_id: str
    abs_path: str
    score: float
    tg_date_iso: str
    src: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iter_memes() -> Iterable[Path]:
    if not RAW_DIR.exists():
        return
    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() in IMAGE_EXT:
            yield p


def _meta_path(p: Path) -> Path:
    return Path(str(p) + ".meta.json")


def _read_meta(p: Path) -> Optional[dict]:
    mp = _meta_path(p)
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_posted(user_id: int, feed: str) -> Set[str]:
    if not POSTED_TSV.exists():
        return set()

    out: Set[str] = set()

    for line in POSTED_TSV.read_text(encoding="utf-8").splitlines():

        if not line.strip():
            continue

        if line.startswith("timestamp"):
            continue

        parts = line.split("\t")

        if len(parts) < 4:
            continue

        ts, u, item, f = parts[0], parts[1], parts[2], parts[3]

        if str(u) == str(user_id) and f == feed:
            out.add(item)

    return out


def _recency_score(age_h: float) -> float:
    return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)


def rank_memes(user_id: int, n: int, feed: str = "feed_memes") -> List[MemeItem]:

    now = _now_utc()
    cut = now - timedelta(hours=MAX_AGE_HOURS)

    posted = _load_posted(user_id, feed)

    out: List[MemeItem] = []

    for p in _iter_memes():

        item_id = p.relative_to(RAW_DIR).as_posix()

        if item_id in posted:
            continue

        meta = _read_meta(p)

        if not meta:
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())

        if not tg_dt:
            continue

        if tg_dt < cut:
            continue

        age_h = max(0.0, (now - tg_dt).total_seconds() / 3600.0)

        score = _recency_score(age_h)

        out.append(
            MemeItem(
                item_id=item_id,
                abs_path=str(p),
                score=float(score),
                tg_date_iso=tg_dt.isoformat(),
                src=(meta.get("src") or "UNKNOWN"),
            )
        )

    out.sort(key=lambda x: x.score, reverse=True)

    return out[:n]
