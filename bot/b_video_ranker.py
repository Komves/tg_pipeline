from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import numpy as np


# --- paths ---
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
RAW_DIR = DATA_DIR / "raw"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT / "tg_pipeline" / "out" / "profiles" / "b_profile_ok.npy"

# optional whitelist (can be missing)
B_SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "b_video_sources.txt"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Telegram bot API: safe upper bound (real limit varies)
MAX_BYTES = 45 * 1024 * 1024  # 45MB

MAX_AGE_HOURS = 96.0
RECENCY_K = 1.0
RECENCY_TAU_H = 24.0


@dataclass(frozen=True)
class BVideoItem:
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


def _iter_videos() -> Iterable[Path]:
    if not RAW_DIR.exists():
        return
    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() in VIDEO_EXT:
            yield p


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


def _norm_src(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("https://t.me/"):
        return s.rstrip("/")
    if s.startswith("t.me/"):
        return ("https://" + s).rstrip("/")
    if s.startswith("@"):
        return ("https://t.me/" + s[1:]).rstrip("/")
    return s.rstrip("/")


def _load_whitelist() -> Optional[Set[str]]:
    if not B_SOURCES_FILE.exists():
        return None
    out: Set[str] = set()
    for line in B_SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        out.add(_norm_src(t))
    return out if out else None


def _recency_score(age_h: float) -> float:
    return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)


def _load_profile() -> np.ndarray:
    if not PROFILE_PATH.exists():
        raise RuntimeError(f"B profile not found: {PROFILE_PATH}")
    v = np.load(PROFILE_PATH)
    v = v.astype(np.float32).reshape(-1)
    v = v / (np.linalg.norm(v) + 1e-12)
    return v


def _load_item_emb(meta: dict) -> Optional[np.ndarray]:
    """
    Expect embedding in meta:
      meta["clip_emb"] = [float,...]
    If not present -> None
    """
    arr = meta.get("clip_emb")
    if not arr:
        return None
    try:
        v = np.asarray(arr, dtype=np.float32).reshape(-1)
        if v.size < 32:
            return None
        v = v / (np.linalg.norm(v) + 1e-12)
        return v
    except Exception:
        return None


def rank_b_videos(user_id: int, n: int, *, feed: str = "feed_b_video") -> List[BVideoItem]:
    profile = _load_profile()
    whitelist = _load_whitelist()

    now = _now_utc()
    cut = now - timedelta(hours=MAX_AGE_HOURS)

    posted = _load_posted(user_id, feed)
    out: List[BVideoItem] = []

    for p in _iter_videos():
        # size filter for Telegram
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
        except Exception:
            continue

        item_id = p.relative_to(RAW_DIR).as_posix()
        if item_id in posted:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        # optional src whitelist
        src = _norm_src(meta.get("src") or "UNKNOWN")
        if whitelist is not None and src not in whitelist:
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())
        if not tg_dt or tg_dt < cut:
            continue

        # MUST have embedding to be "B"
        emb = _load_item_emb(meta)
        if emb is None or emb.shape != profile.shape:
            continue

        sim = float(np.dot(profile, emb))  # cosine similarity
        age_h = max(0.0, (now - tg_dt).total_seconds() / 3600.0)
        score = sim + _recency_score(age_h)

        out.append(
            BVideoItem(
                item_id=item_id,
                abs_path=str(p),
                score=score,
                tg_date_iso=tg_dt.isoformat(),
                src=src,
            )
        )

    out.sort(key=lambda x: x.score, reverse=True)
    return out[: max(0, n)]
