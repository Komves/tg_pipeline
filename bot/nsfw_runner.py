import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from nsfw_client import score_image

RAW = Path(os.getenv("DATA_DIR", "/data")) / "raw"
TMP = Path("/tmp/nsfw_frames")
TMP.mkdir(parents=True, exist_ok=True)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


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


def _extract_frames(video: Path, out_dir: Path, n: int = 3) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # grab n frames evenly (ffmpeg selects by fps; we just sample early)
    # -frames:v n outputs n images
    out_pattern = out_dir / (video.stem + "_%02d.jpg")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", "fps=1/2",  # 1 frame per 2 sec
        "-frames:v", str(n),
        str(out_pattern),
    ]
    subprocess.run(cmd, check=False)
    frames = sorted(out_dir.glob(video.stem + "_*.jpg"))
    return frames[:n]


def _score_video_b(video: Path) -> Optional[float]:
    frames = _extract_frames(video, TMP, n=3)
    if not frames:
        return None

    scores = []
    for fr in frames:
        res = score_image(str(fr))
        if not res:
            continue

        # пытаемся достать хоть какой-то score из json
        # (структуры у разных API отличаются)
        val = None
        if isinstance(res, dict):
            # часто бывает { "results": { "porn": 0.12, ... } } или { "porn": 0.12 }
            if "porn" in res:
                val = res.get("porn")
            elif "results" in res and isinstance(res["results"], dict):
                val = res["results"].get("porn") or res["results"].get("nsfw")
            elif "nsfw" in res:
                val = res.get("nsfw")

        try:
            if val is not None:
                scores.append(float(val))
        except Exception:
            continue

    if not scores:
        return None
    return max(scores)


def score_missing_b(hours: int = 72, limit: int = 50) -> int:
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)

    done = 0
    scanned = 0

    for p in RAW.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXT:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        tg = (meta.get("tg_date") or "").strip()
        dt = _parse_iso(tg) if tg else None
        if dt and dt < cut:
            continue

        if meta.get("b_nsfw_score") is not None:
            continue

        scanned += 1
        score = _score_video_b(p)

        if score is None:
            # не смогли скорить — просто пропускаем, не падаем
            continue

        meta["b_nsfw_score"] = score
        meta["b_nsfw_scored_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(p, meta)

        done += 1
        if done >= limit:
            break

    print(f"[nsfw] scanned={scanned} scored={done}")
    return done
