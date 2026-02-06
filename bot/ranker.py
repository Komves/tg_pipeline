from __future__ import annotations

import csv
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple


DATA_DIR = Path("/data")
RAW_DIR = DATA_DIR / "raw"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"
FEEDBACK_TSV = DATA_DIR / "a_feedback_master.tsv"

CAT_A_VIDEO = "A_VIDEO"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

MAX_AGE_HOURS = 24.0
RECENCY_K = 2.0
RECENCY_TAU_H = 12.0

# реклама по caption (минимальный набор, чтобы не зарезать нормальные)
AD_KEYWORDS = {
    "казино", "casino", "ставк", "ставка", "bet", "bonus", "бонус",
    "промокод", "promo", "промо",
    "реклама", "advert", "advertising",
}


@dataclass(frozen=True)
class RankedItem:
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


def _iter_video_files() -> Iterable[Path]:
    if not RAW_DIR.exists():
        return
    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() in VIDEO_EXT:
            yield p


def _meta_path_for_media(p: Path) -> Path:
    return Path(str(p) + ".meta.json")


def _read_meta(p: Path) -> Optional[dict]:
    mp = _meta_path_for_media(p)
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(p: Path, meta: dict) -> None:
    mp = _meta_path_for_media(p)
    try:
        mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _looks_like_ad(meta: dict) -> bool:
    cap = (meta.get("caption") or "")
    s = cap.lower()
    return any(k in s for k in AD_KEYWORDS)


# ---------- audio (cached) ----------
def _ffprobe_has_audio(p: Path) -> Optional[bool]:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(p)],
            stderr=subprocess.DEVNULL,
        )
        j = json.loads(out)
        return len(j.get("streams", [])) > 0
    except Exception:
        return None


def _has_audio_cached(p: Path, meta: dict) -> bool:
    if "has_audio" in meta:
        return bool(meta["has_audio"])

    res = _ffprobe_has_audio(p)
    if res is None:
        # если ffprobe недоступен — не режем пул
        return True

    meta["has_audio"] = bool(res)
    _write_meta(p, meta)
    return bool(res)


# ---------- perceptual dedupe (dhash) ----------
def _run_ffmpeg_frame(p: Path) -> Optional[bytes]:
    """
    Берём кадр на 1-й секунде, гоним в 9x8 grayscale raw.
    Это даёт 72 байта (9*8). Потом делаем dhash 8x8 = 64 бита.
    """
    try:
        # -ss 1.0: кадр не с самого начала (часто заставки/логотипы)
        # scale=9:8,format=gray -> rawvideo
        cmd = [
            "ffmpeg",
            "-v", "error",
            "-ss", "1.0",
            "-i", str(p),
            "-frames:v", "1",
            "-vf", "scale=9:8,format=gray",
            "-f", "rawvideo",
            "pipe:1",
        ]
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except Exception:
        return None


def _dhash_from_gray9x8(buf: bytes) -> Optional[str]:
    if not buf or len(buf) < 72:
        return None
    # rows 8, cols 9
    # dhash: сравниваем пиксель (x) с (x+1) по 8x8
    bits = []
    idx = 0
    for y in range(8):
        row = list(buf[idx:idx+9])
        idx += 9
        for x in range(8):
            bits.append(1 if row[x] > row[x+1] else 0)
    # 64 bits -> hex 16 chars
    h = 0
    for b in bits:
        h = (h << 1) | b
    return f"{h:016x}"


def _phash_cached(p: Path, meta: dict) -> Optional[str]:
    """
    Кэшируем dhash в meta.json как "dhash".
    """
    v = meta.get("dhash")
    if isinstance(v, str) and len(v) == 16:
        return v

    frame = _run_ffmpeg_frame(p)
    dh = _dhash_from_gray9x8(frame) if frame else None
    if dh:
        meta["dhash"] = dh
        _write_meta(p, meta)
    return dh


# ---------- posted / feedback ----------
def _load_posted_set(user_id: int, feed: str) -> Set[str]:
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


def _load_seen_ids(user_id: int, feed: str, limit: int = 600) -> List[str]:
    """
    Собираем последние seen item_id из posted+feedback (ограниченно, чтобы не убить CPU).
    """
    seen: List[str] = []

    # feedback (последние limit)
    if FEEDBACK_TSV.exists():
        lines = FEEDBACK_TSV.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip() or line.startswith("timestamp"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            _ts, u, item, _a = parts[0], parts[1], parts[2], parts[3]
            if str(u) == str(user_id):
                seen.append(item)
                if len(seen) >= limit:
                    break

    # posted (последние limit)
    if POSTED_TSV.exists() and len(seen) < limit:
        lines = POSTED_TSV.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip() or line.startswith("timestamp"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            _ts, u, item, f = parts[0], parts[1], parts[2], parts[3]
            if str(u) == str(user_id) and f == feed:
                seen.append(item)
                if len(seen) >= limit:
                    break

    # уникализируем, сохраняя порядок
    out = []
    s = set()
    for x in seen:
        if x not in s:
            s.add(x)
            out.append(x)
    return out


def _build_seen_dhash(user_id: int, feed: str) -> Set[str]:
    """
    Реально считаем dhash для уже виденных (ограниченно), чтобы репосты/перекоды отсеивались.
    """
    seen_ids = _load_seen_ids(user_id, feed, limit=600)
    out: Set[str] = set()
    for item_id in seen_ids:
        p = RAW_DIR / item_id
        if not p.exists():
            continue
        meta = _read_meta(p)
        if not meta:
            continue
        dh = _phash_cached(p, meta)
        if dh:
            out.add(dh)
    return out


def _recency_score(age_h: float) -> float:
    return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)


def rank_top_n(user_id: int, category: str, n: int, *, feed: str = "feed_a_video") -> List[RankedItem]:
    if category != CAT_A_VIDEO:
        return []

    now = _now_utc()
    cut = now - timedelta(hours=MAX_AGE_HOURS)

    posted = _load_posted_set(user_id, feed)
    seen_dhash = _build_seen_dhash(user_id, feed)

    items: List[RankedItem] = []

    for p in _iter_video_files():
        item_id = p.relative_to(RAW_DIR).as_posix()
        if item_id in posted:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())
        if not tg_dt or tg_dt < cut:
            continue

        if _looks_like_ad(meta):
            continue

        if not _has_audio_cached(p, meta):
            continue

        # dedupe by perceptual hash
        dh = _phash_cached(p, meta)
        if dh and dh in seen_dhash:
            continue

        age_h = max(0.0, (now - tg_dt).total_seconds() / 3600.0)
        score = _recency_score(age_h)

        items.append(
            RankedItem(
                item_id=item_id,
                abs_path=str(p),
                score=float(score),
                tg_date_iso=tg_dt.isoformat(),
                src=(meta.get("src") or "UNKNOWN"),
            )
        )

    items.sort(key=lambda x: x.score, reverse=True)
    return items[: max(0, n)]
