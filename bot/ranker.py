from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


DEFAULT_DATA_DIR = Path("/data")
DEFAULT_RAW_DIR = DEFAULT_DATA_DIR / "raw"
DEFAULT_FEEDBACK_TSV = DEFAULT_DATA_DIR / "a_feedback_master.tsv"
DEFAULT_POSTED_TSV = DEFAULT_DATA_DIR / "a_posted_master.tsv"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

CAT_A_VIDEO = "A_VIDEO"
CAT_A_MEME = "A_MEME"

ACTION_WEIGHT = {"like": 1.0, "dislike": -1.0, "ban": -3.0}
BASE_SCORE = 0.0

RECENCY_K = 1.25
RECENCY_TAU_H = 18.0  # ~1 day

# --- hard freshness cutoff for A_VIDEO (download time / mtime) ---
A_VIDEO_MAX_AGE_HOURS = 24.0

# --- anti-ad keywords (path/filename heuristic) ---
AD_KEYWORDS = {
    "казино", "casino", "ставк", "bet", "bonus", "бонус", "промо", "promo",
    "реклама", "reklama", "ad", "ads", "sale", "скидк", "discount",
    "t.me/", "tg://", "подпиш", "подпис", "канал", "channel",
}


@dataclass(frozen=True)
class RankedItem:
    item_id: str
    abs_path: str
    category: str
    score: float
    mtime_iso: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_parse_iso(ts: str) -> Optional[datetime]:
    ts = (ts or "").strip()
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iter_media_files(raw_dir: Path) -> Iterable[Path]:
    if not raw_dir.exists():
        return
    for p in raw_dir.rglob("*"):
        if p.is_file():
            ext = p.suffix.lower()
            if ext in VIDEO_EXT or ext in IMAGE_EXT:
                yield p


def _category_for_path(p: Path) -> Optional[str]:
    ext = p.suffix.lower()
    if ext in VIDEO_EXT:
        return CAT_A_VIDEO
    if ext in IMAGE_EXT:
        return CAT_A_MEME
    return None


def _item_id_from_abs_path(raw_dir: Path, abs_path: Path) -> str:
    rel = abs_path.relative_to(raw_dir)
    return rel.as_posix()


def load_feedback(
    feedback_tsv: Path = DEFAULT_FEEDBACK_TSV,
) -> List[Tuple[Optional[datetime], int, str, str]]:
    if not feedback_tsv.exists():
        return []

    rows: List[Tuple[Optional[datetime], int, str, str]] = []
    with feedback_tsv.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for parts in reader:
            if not parts:
                continue
            if parts[0].strip().lower() == "timestamp":
                continue
            if len(parts) < 4:
                continue
            ts_s, user_s, item_id, action = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            try:
                user_id = int(user_s)
            except Exception:
                continue
            dt = _safe_parse_iso(ts_s)
            rows.append((dt, user_id, item_id, action.lower()))
    return rows


def build_user_taste_index(
    feedback_rows: List[Tuple[Optional[datetime], int, str, str]]
) -> Dict[int, Dict[str, float]]:
    idx: Dict[int, Dict[str, float]] = {}
    for _dt, user_id, item_id, action in feedback_rows:
        w = ACTION_WEIGHT.get(action, 0.0)
        if w == 0.0:
            continue
        idx.setdefault(user_id, {})
        idx[user_id][item_id] = idx[user_id].get(item_id, 0.0) + w
    return idx


def build_user_ban_set(
    feedback_rows: List[Tuple[Optional[datetime], int, str, str]]
) -> Dict[int, set]:
    bans: Dict[int, set] = {}
    for _dt, user_id, item_id, action in feedback_rows:
        if action == "ban":
            bans.setdefault(user_id, set()).add(item_id)
    return bans


def load_posted_set(
    user_id: int,
    feed: str,
    posted_tsv: Path = DEFAULT_POSTED_TSV,
) -> Set[str]:
    if not posted_tsv.exists():
        return set()

    out: Set[str] = set()
    with posted_tsv.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for parts in reader:
            if not parts:
                continue
            if parts[0].strip().lower() == "timestamp":
                continue
            if len(parts) < 4:
                continue
            _ts, u_s, item_id, feed_s = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            try:
                u = int(u_s)
            except Exception:
                continue
            if u == user_id and feed_s == feed:
                out.add(item_id)
    return out


def _recency_boost(abs_path: Path, now: datetime) -> float:
    try:
        mtime = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=timezone.utc)
        age_h = max(0.0, (now - mtime).total_seconds() / 3600.0)
        return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)
    except Exception:
        return 0.0


def _age_hours(abs_path: Path, now: datetime) -> Optional[float]:
    try:
        mtime = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=timezone.utc)
        return max(0.0, (now - mtime).total_seconds() / 3600.0)
    except Exception:
        return None


def _looks_like_ad(p: Path) -> bool:
    s = p.as_posix().lower()
    return any(k in s for k in AD_KEYWORDS)


def rank_top_n(
    user_id: int,
    category: str,
    n: int,
    *,
    feed: str = "feed_a_video",
    raw_dir: Path = DEFAULT_RAW_DIR,
    feedback_tsv: Path = DEFAULT_FEEDBACK_TSV,
    posted_tsv: Path = DEFAULT_POSTED_TSV,
) -> List[RankedItem]:
    now = _now_utc()

    feedback = load_feedback(feedback_tsv)
    taste_idx = build_user_taste_index(feedback)
    ban_idx = build_user_ban_set(feedback)

    user_taste = taste_idx.get(user_id, {})
    user_bans = ban_idx.get(user_id, set())
    already_posted = load_posted_set(user_id=user_id, feed=feed, posted_tsv=posted_tsv)

    candidates: List[RankedItem] = []
    for p in _iter_media_files(raw_dir):
        cat = _category_for_path(p)
        if cat != category:
            continue

        if _looks_like_ad(p):
            continue

        # hard freshness cutoff only for A_VIDEO
        if cat == CAT_A_VIDEO:
            ah = _age_hours(p, now)
            if ah is not None and ah > A_VIDEO_MAX_AGE_HOURS:
                continue

        item_id = _item_id_from_abs_path(raw_dir, p)

        if item_id in user_bans:
            continue
        if item_id in already_posted:
            continue

        taste_score = user_taste.get(item_id, 0.0)
        score = BASE_SCORE + taste_score + _recency_boost(p, now)

        try:
            mtime_iso = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            mtime_iso = ""

        candidates.append(
            RankedItem(
                item_id=item_id,
                abs_path=str(p),
                category=cat,
                score=float(score),
                mtime_iso=mtime_iso,
            )
        )

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[: max(0, n)]
