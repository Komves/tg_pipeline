from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Set, List

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
RAW_DIR = DATA_DIR / "raw"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"

REPO_ROOT = Path(__file__).resolve().parents[1]
B_SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "b_video_sources.txt"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

MAX_AGE_HOURS = 72


@dataclass(frozen=True)
class BVideoItem:
    item_id: str
    abs_path: str
    score: float
    tg_date_iso: str
    src: str


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _norm_src(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("https://t.me/"):
        return s.rstrip("/")
    if s.startswith("t.me/"):
        return ("https://" + s).rstrip("/")
    if s.startswith("@"):
        return ("https://t.me/" + s[1:]).rstrip("/")
    return s.rstrip("/")


def _load_whitelist() -> Set[str]:
    if not B_SOURCES_FILE.exists():
        return set()
    out: Set[str] = set()
    for line in B_SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        out.add(_norm_src(t))
    return out


def _load_posted(user_id: int, feed: str) -> Set[str]:
    if not POSTED_TSV.exists():
        return set()
    out: Set[str] = set()
    for line in POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        _ts, u, item, f = parts[0], parts[1], parts[2], parts[3]
        if str(u) == str(user_id) and f == feed:
            out.add(item)
    return out


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


def rank_b_videos(user_id: int, n: int, *, feed: str = "feed_b_video") -> List[BVideoItem]:
    wl = _load_whitelist()
    posted = _load_posted(user_id, feed)

    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=MAX_AGE_HOURS)

    out: List[BVideoItem] = []

    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() not in VIDEO_EXT:
            continue

        item_id = p.relative_to(RAW_DIR).as_posix()
        if item_id in posted:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        src = _norm_src(meta.get("src") or "")
        if wl and src not in wl:
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())
        if not tg_dt or tg_dt < cut:
            continue

        score = meta.get("b_nsfw_score")
        if score is None:
            continue

        out.append(
            BVideoItem(
                item_id=item_id,
                abs_path=str(p),
                score=float(score),
                tg_date_iso=tg_dt.isoformat(),
                src=src,
            )
        )

    out.sort(key=lambda x: x.score, reverse=True)
    return out[: max(0, n)]
