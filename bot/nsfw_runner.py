import json
import subprocess
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


def _write_meta(meta_path: Path, j: dict) -> None:
    meta_path.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")


def _extract_one_frame(video_path: Path) -> Optional[Path]:
    """
    Extract 1 frame with ffmpeg (lightweight).
    Writes to /tmp/nsfw_frames/<stem>_nsfw.jpg
    """
    out = FRAME_DIR / (video_path.name.replace(video_path.suffix, "") + "_nsfw.jpg")
    if out.exists() and out.stat().st_size > 0:
        return out

    # Take a frame at ~1.0 sec (works for short vids too; ffmpeg will pick closest)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "1.0",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-2",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None

    if out.exists() and out.stat().st_size > 0:
        return out
    return None


def _candidate_videos(hours: int) -> List[Tuple[Path, Path, dict]]:
    """
    Return (video_path, meta_path, meta_json) for videos within last <hours>
    that don't have b_nsfw_score yet.
    """
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)

    out = []
    for v in RAW_DIR.rglob("*"):
        if not v.is_file():
            continue
        if v.suffix.lower() not in VIDEO_EXT:
            continue

        mp = Path(str(v) + ".meta.json")  # <video>.meta.json
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


def score_missing_b(hours: int = 72, limit: int = 3) -> int:
    """
    For up to <limit> videos:
      1) extract 1 frame via ffmpeg
      2) send to RapidAPI NSFW
      3) write b_nsfw_score into <video>.meta.json
    """
    cands = _candidate_videos(hours)
    done = 0

    for v, mp, j in cands:
        if done >= limit:
            break

        frame = _extract_one_frame(v)
        if frame is None:
            continue

        try:
            nsfw = float(score_image(str(frame)))
        except Exception as e:
            print(f"[nsfw] error scoring {frame}: {e}")
            continue

        j["b_nsfw_score"] = nsfw
        j["b_nsfw_scored_at"] = datetime.now(timezone.utc).isoformat()
        j["nsfw_frame"] = str(frame)
        _write_meta(mp, j)

        done += 1
        print(f"[nsfw] scored {v.name}: {nsfw:.4f}")

    return done
