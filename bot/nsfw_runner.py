from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Set

from nsfw_client import score_image


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
RAW_DIR = DATA_DIR / "raw"

REPO_ROOT = Path(__file__).resolve().parents[1]
B_SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "b_video_sources.txt"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Extract N frames per video for scoring
FRAMES_N = int(os.getenv("B_FRAMES_N", "3"))
MAX_FILE_MB = int(os.getenv("B_MAX_FILE_MB", "80"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


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


def _write_meta(p: Path, meta: dict) -> None:
    mp = _meta_path(p)
    mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _extract_frames(video: Path, out_dir: Path, n: int) -> list[Path]:
    """
    Extract n frames from the video (uniform-ish) as JPG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str(out_dir / "f_%03d.jpg")

    # fps=1 + cap by frames:v -> simple and stable
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", "fps=1,scale=640:-1",
        "-frames:v", str(n),
        "-q:v", "3",
        out_pattern,
    ]
    subprocess.run(cmd, check=False)
    frames = sorted(out_dir.glob("f_*.jpg"))
    return frames[:n]


def nsfw_score_video(video: Path) -> Optional[float]:
    """
    Returns max NSFW score across extracted frames (0..1).
    """
    if not _ffmpeg_exists():
        raise RuntimeError("ffmpeg/ffprobe not found in container")

    with tempfile.TemporaryDirectory() as td:
        frames = _extract_frames(video, Path(td), FRAMES_N)
        if not frames:
            return None

        scores = []
        for fr in frames:
            try:
                scores.append(score_image(str(fr)))
            except Exception:
                continue

        if not scores:
            return None

        return float(max(scores))


def score_missing_b(hours: int = 72, limit: int = 50) -> int:
    """
    For B videos (by src whitelist), if meta has no b_nsfw_score -> compute and store.
    """
    wl = _load_whitelist()
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)

    done = 0

    for p in RAW_DIR.rglob("*"):
        if done >= limit:
            break
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() not in VIDEO_EXT:
            continue

        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except Exception:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        src = _norm_src(meta.get("src") or "")
        if wl and src not in wl:
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())
        if tg_dt and tg_dt < cut:
            continue

        if meta.get("b_nsfw_score") is not None:
            continue

        s = nsfw_score_video(p)
        if s is None:
            continue

        meta["b_nsfw_score"] = float(s)
        meta["b_nsfw_frames"] = int(FRAMES_N)
        _write_meta(p, meta)
        done += 1

    return done


if __name__ == "__main__":
    n = score_missing_b(
        hours=int(os.getenv("B_SCORE_HOURS", "72")),
        limit=int(os.getenv("B_SCORE_LIMIT", "50")),
    )
    print("SCORED:", n)
