import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple, List

from nsfw_client import score_image

RAW_DIR = Path("/data/raw")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
FRAME_DIR = Path("/tmp/nsfw_frames")
FRAME_DIR.mkdir(parents=True, exist_ok=True)


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _pick_frame_path(meta_path: Path) -> Optional[Path]:
    """
    We try to find already-extracted frame if your pipeline saved it.
    If not found, return None (we will fall back to scoring thumbnail/image only when available).
    """
    # If meta already has a cached frame path - use it
    try:
        j = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    fp = (j.get("nsfw_frame") or "").strip()
    if fp:
        p = Path(fp)
        if p.exists():
            return p
    return None


def _candidate_videos(hours: int) -> List[Tuple[Path, Path, dict]]:
    """
    Return list of tuples: (video_path, meta_path, meta_json)
    Only videos with existing <video>.meta.json and tg_date inside last <hours>.
    Only those without b_nsfw_score yet.
    """
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)

    out = []
    for v in RAW_DIR.rglob("*"):
        if not v.is_file():
            continue
        if v.suffix.lower() not in VIDEO_EXT:
            continue

        mp = Path(str(v) + ".meta.json")  # IMPORTANT: <video>.meta.json
        if not mp.exists():
            continue

        try:
            j = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue

        if j.get("b_nsfw_score") is not None:
            continue

        tg = (j.get("tg_date") or "").strip()
        dt = _parse_iso(tg) if tg else None
        if dt and dt < cut:
            continue

        out.append((v, mp, j))
    return out


def _write_meta(meta_path: Path, j: dict) -> None:
    meta_path.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")


def score_missing_b(hours: int = 72, limit: int = 3) -> int:
    """
    Scores up to <limit> new videos for NSFW, saving b_nsfw_score into <video>.meta.json.
    Uses a single still image per video:
      - if meta has cached frame path -> use it
      - else -> skip (we don't extract frames here to keep it lightweight)
    """
    cands = _candidate_videos(hours)
    done = 0

    for v, mp, j in cands:
        if done >= limit:
            break

        frame = _pick_frame_path(mp)
        if frame is None:
            # No frame prepared -> skip this video for now
            continue

        try:
            nsfw = float(score_image(str(frame)))
        except Exception as e:
            print(f"[nsfw] error scoring {frame}: {e}")
            continue

        j["b_nsfw_score"] = nsfw
        j["b_nsfw_scored_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(mp, j)

        done += 1
        print(f"[nsfw] scored {v.name}: {nsfw:.4f} (frame={frame.name})")

    return done
