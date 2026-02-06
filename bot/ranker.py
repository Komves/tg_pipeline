# bot/ranker.py
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# --- Canonical paths (match your brief) ---
DEFAULT_DATA_DIR = Path("/data")
DEFAULT_RAW_DIR = DEFAULT_DATA_DIR / "raw"
DEFAULT_FEEDBACK_TSV = DEFAULT_DATA_DIR / "a_feedback_master.tsv"

# --- Minimal media type mapping ---
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Category names (keep simple for now)
CAT_A_VIDEO = "A_VIDEO"
CAT_A_MEME = "A_MEME"

# Feedback weights (tune later)
ACTION_WEIGHT = {
    "like": 1.0,
    "dislike": -1.0,
    "ban": -3.0,
}

# If item has no feedback, it still can appear via recency
BASE_SCORE = 0.0

# Recency boost params
# score += RECENCY_K * exp(-age_hours / RECENCY_TAU_H)
RECENCY_K = 0.35
RECENCY_TAU_H = 72.0  # ~3 days half-ish decay


@dataclass(frozen=True)
class RankedItem:
    item_id: str                 # stable: relative path from /data/raw
    abs_path: str                # full path for sending
    category: str                # A_VIDEO / A_MEME
    score: float                 # final score
    mtime_iso: str               # for debug / logging


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_parse_iso(ts: str) -> Optional[datetime]:
    # Accept "2026-02-06T07:12:33" (no tz) or with tz
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
    # RAW canonical structure: /data/raw/MIX/<channel>/<file>
    # But we don't hard-require MIX – we just walk everything under raw/
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
    # Stable ID = relative path under /data/raw
    rel = abs_path.relative_to(raw_dir)
    # Normalize to POSIX style for stable IDs across OS
    return rel.as_posix()


def load_feedback(
    feedback_tsv: Path = DEFAULT_FEEDBACK_TSV,
) -> List[Tuple[Optional[datetime], int, str, str]]:
    """
    Returns list of (timestamp_utc|None, user_id, item_id, action)
    TSV format (canonical):
        timestamp    user_id    item_id    action
    """
    if not feedback_tsv.exists():
        return []

    rows: List[Tuple[Optional[datetime], int, str, str]] = []
    with feedback_tsv.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for parts in reader:
            if not parts:
                continue
            # tolerate header row
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
    """
    user_id -> item_id -> cumulative weight
    """
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
    """
    user_id -> set(item_id) that is banned by this user
    """
    bans: Dict[int, set] = {}
    for _dt, user_id, item_id, action in feedback_rows:
        if action == "ban":
            bans.setdefault(user_id, set()).add(item_id)
    return bans


def _recency_boost(abs_path: Path, now: datetime) -> float:
    try:
        mtime = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=timezone.utc)
        age_h = max(0.0, (now - mtime).total_seconds() / 3600.0)
        return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)
    except Exception:
        return 0.0


def rank_top_n(
    user_id: int,
    category: str,
    n: int,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    feedback_tsv: Path = DEFAULT_FEEDBACK_TSV,
) -> List[RankedItem]:
    """
    Minimal ranker:
      score = BASE_SCORE + user_item_weight + recency_boost
    Excludes items banned by user.
    """
    now = _now_utc()

    feedback = load_feedback(feedback_tsv)
    taste_idx = build_user_taste_index(feedback)
    ban_idx = build_user_ban_set(feedback)

    user_taste = taste_idx.get(user_id, {})
    user_bans = ban_idx.get(user_id, set())

    candidates: List[RankedItem] = []
    for p in _iter_media_files(raw_dir):
        cat = _category_for_path(p)
        if cat != category:
            continue

        item_id = _item_id_from_abs_path(raw_dir, p)
        if item_id in user_bans:
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


def debug_explain_top(
    user_id: int,
    category: str,
    n: int,
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    feedback_tsv: Path = DEFAULT_FEEDBACK_TSV,
) -> str:
    """
    Human-readable explanation (for /test or logs).
    """
    items = rank_top_n(user_id, category, n, raw_dir=raw_dir, feedback_tsv=feedback_tsv)
    lines = [f"TOP {n} for user={user_id} cat={category}"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i:02d}) score={it.score:.4f} mtime={it.mtime_iso} id={it.item_id}")
    return "\n".join(lines)
