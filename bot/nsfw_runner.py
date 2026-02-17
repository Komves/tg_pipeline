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

MAX_VIDEOS_PER_RUN = int(os.getenv("NSFW_MAX_VIDEOS_PER_RUN", "10"))
MAX_FRAMES_PER_VIDEO = int(os.getenv("NSFW_MAX_FRAMES_PER_VIDEO", "3"))

# ЖЁСТКИЙ дневной лимит
MAX_CALLS_PER_DAY = int(os.getenv("NSFW_MAX_CALLS_PER_DAY", "90"))

# файл учёта
CALLS_FILE = Path(os.getenv("DATA_DIR", "/data")) / "nsfw_calls.json"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_calls() -> dict:
    if not CALLS_FILE.exists():
        return {}
    try:
        return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_calls(d: dict) -> None:
    try:
        CALLS_FILE.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _calls_today() -> int:
    d = _load_calls()
    return int(d.get(_today_str(), 0))


def _inc_calls(n: int) -> None:
    d = _load_calls()
    today = _today_str()
    d[today] = int(d.get(today, 0)) + n
    _save_calls(d)


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
    out_pattern = out_dir / (video.stem + "_%02d.jpg")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", "fps=1/2",
        "-frames:v", str(n),
        str(out_pattern),
    ]

    subprocess.run(cmd, check=False)
    frames = sorted(out_dir.glob(video.stem + "_*.jpg"))
    return frames[:n]


def _score_video_b(video: Path) -> Optional[float]:
    frames = _extract_frames(video, TMP, MAX_FRAMES_PER_VIDEO)
    if not frames:
        return None

    scores = []

    for fr in frames:
        if _calls_today() >= MAX_CALLS_PER_DAY:
            print("[nsfw] daily call limit reached -> stop scoring")
            return None

        res = score_image(str(fr))

        if res is None:
            continue

        _inc_calls(1)

        val = None

        if isinstance(res, dict):
            if "porn" in res:
                val = res.get("porn")
            elif "results" in res:
                val = res["results"].get("porn") or res["results"].get("nsfw")
            elif "nsfw" in res:
                val = res.get("nsfw")

        try:
            if val is not None:
                scores.append(float(val))
        except Exception:
            pass

    if not scores:
        return None

    return max(scores)


def score_missing_b(hours: int = 72) -> int:

    if _calls_today() >= MAX_CALLS_PER_DAY:
        print(f"[nsfw] daily limit reached ({_calls_today()}/{MAX_CALLS_PER_DAY})")
        return 0

    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)

    done = 0
    scanned = 0

    for p in RAW.rglob("*"):

        if done >= MAX_VIDEOS_PER_RUN:
            break

        if not p.is_file():
            continue

        if p.suffix.lower() not in VIDEO_EXT:
            continue

        meta = _read_meta(p)

        if not meta:
            continue

        tg = (meta.get("tg_date") or "").strip()
        dt = _parse_iso(tg)

        if dt and dt < cut:
            continue

        if meta.get("b_nsfw_score") is not None:
            continue

        scanned += 1

        score = _score_video_b(p)

        if score is None:
            continue

        meta["b_nsfw_score"] = score
        meta["b_nsfw_scored_at"] = datetime.now(timezone.utc).isoformat()

        _write_meta(p, meta)

        done += 1

    print(
        f"[nsfw] scanned={scanned} scored={done} calls_today={_calls_today()}/{MAX_CALLS_PER_DAY}"
    )

    return done
