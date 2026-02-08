import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# IMPORTANT: import from bot.nsfw_client (Render реально грузит этот модуль)
from bot.nsfw_client import score_image, QuotaExhausted, TemporaryNsfwError

RAW = Path(os.getenv("DATA_DIR", "/data")) / "raw"
TMP = Path("/tmp/nsfw_frames")
TMP.mkdir(parents=True, exist_ok=True)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Hard limits per run (stop conditions)
MAX_API_CALLS_PER_RUN = int(os.getenv("NSFW_MAX_API_CALLS_PER_RUN", "25"))
MAX_VIDEOS_PER_RUN = int(os.getenv("NSFW_MAX_VIDEOS_PER_RUN", "3"))
MAX_FRAMES_PER_VIDEO = int(os.getenv("NSFW_MAX_FRAMES_PER_VIDEO", "3"))


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _read_meta(media_path: Path) -> Optional[dict]:
    mp = Path(str(media_path) + ".meta.json")
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(media_path: Path, meta: dict) -> None:
    mp = Path(str(media_path) + ".meta.json")
    mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _extract_frames(video: Path, out_dir: Path, n: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clean old frames for this video stem to avoid mixing results
    for old in out_dir.glob(video.stem + "_*.jpg"):
        try:
            old.unlink()
        except Exception:
            pass

    out_pattern = out_dir / (video.stem + "_%02d.jpg")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", "fps=1/2",          # 1 frame per 2 sec
        "-frames:v", str(n),       # hard cap frames
        str(out_pattern),
    ]
    subprocess.run(cmd, check=False)

    frames = sorted(out_dir.glob(video.stem + "_*.jpg"))
    return frames[:n]


def _extract_score(res: dict) -> Optional[float]:
    # пытаемся достать хоть какой-то score из json
    val = None
    if isinstance(res, dict):
        if "porn" in res:
            val = res.get("porn")
        elif "results" in res and isinstance(res["results"], dict):
            val = res["results"].get("porn") or res["results"].get("nsfw")
        elif "nsfw" in res:
            val = res.get("nsfw")

    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def score_missing_b(hours: int = 72, limit: int = 50) -> int:
    """
    Scores videos missing b_nsfw_score.
    IMPORTANT: never infinite retry.
    Stops early on quota, or when hard limits reached.
    Returns number of videos scored (meta updated).
    """
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)

    api_calls = 0
    scored_videos = 0
    scanned_candidates = 0
    quota_exhausted = False

    # Keep backward compat: 'limit' is max scored videos,
    # but we also apply MAX_VIDEOS_PER_RUN hard cap.
    max_videos_this_run = min(int(limit), MAX_VIDEOS_PER_RUN)

    for p in RAW.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXT:
            continue

        if scored_videos >= max_videos_this_run:
            break
        if api_calls >= MAX_API_CALLS_PER_RUN:
            break

        meta = _read_meta(p)
        if not meta:
            continue

        tg = (meta.get("tg_date") or "").strip()
        dt = _parse_iso(tg) if tg else None
        if dt and dt < cut:
            continue

        if meta.get("b_nsfw_score") is not None:
            continue

        scanned_candidates += 1

        # Extract frames (hard cap)
        frames = _extract_frames(p, TMP, n=MAX_FRAMES_PER_VIDEO)
        if not frames:
            continue

        scores: list[float] = []

        for fr in frames:
            if api_calls >= MAX_API_CALLS_PER_RUN:
                break
            try:
                res = score_image(str(fr))
                api_calls += 1  # count attempt (API call happens inside score_image)
            except QuotaExhausted:
                quota_exhausted = True
                print("[nsfw] quota exhausted -> STOP scoring this run")
                break
            except TemporaryNsfwError as e:
                # no retry loop here; just skip this frame
                print(f"[nsfw] temp error (skip frame): {e}")
                continue
            except Exception as e:
                print(f"[nsfw] unexpected error (skip frame): {e}")
                continue

            if not res:
                continue

            s = _extract_score(res)
            if s is not None:
                scores.append(s)

        if quota_exhausted:
            break

        if not scores:
            continue

        meta["b_nsfw_score"] = max(scores)
        meta["b_nsfw_scored_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(p, meta)
        scored_videos += 1

    print(
        f"[nsfw] scanned_candidates={scanned_candidates} "
        f"scored_videos={scored_videos} api_calls={api_calls} "
        f"quota_exhausted={quota_exhausted}"
    )
    return scored_videos
