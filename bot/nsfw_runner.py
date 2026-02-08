import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from nsfw_client import score_image, QuotaExhausted, TemporaryNsfwError


RAW = Path(os.getenv("DATA_DIR", "/data")) / "raw"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

FALLBACK_SCORE = float(os.getenv("NSFW_FALLBACK_SCORE", "0.0"))


def _parse_iso(s: str):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _read_meta(p: Path):
    mp = Path(str(p) + ".meta.json")
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text())
    except Exception:
        return None


def _write_meta(p: Path, meta: dict):
    mp = Path(str(p) + ".meta.json")
    mp.write_text(json.dumps(meta))


def score_missing_b(hours: int = 72, limit: int = 50) -> int:
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)

    scored = 0
    quota_exhausted = False

    for p in RAW.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXT:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        if meta.get("b_nsfw_score") is not None:
            continue

        tg = meta.get("tg_date")
        dt = _parse_iso(tg) if tg else None
        if dt and dt < cut:
            continue

        try:
            res = score_image(str(p))
            if res:
                meta["b_nsfw_score"] = float(res.get("porn", FALLBACK_SCORE))
            else:
                meta["b_nsfw_score"] = FALLBACK_SCORE

        except QuotaExhausted:
            print("[nsfw] quota exhausted -> applying fallback score")
            meta["b_nsfw_score"] = FALLBACK_SCORE
            quota_exhausted = True

        except Exception as e:
            print(f"[nsfw] error -> fallback score: {e}")
            meta["b_nsfw_score"] = FALLBACK_SCORE

        meta["b_nsfw_scored_at"] = now.isoformat()

        _write_meta(p, meta)

        scored += 1

        if scored >= limit:
            break

        if quota_exhausted:
            break

    print(f"[nsfw] scored={scored} fallback={quota_exhausted}")

    return scored
