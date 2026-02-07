from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from clip_embedder import ensure_meta_clip_emb

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
RAW_DIR = DATA_DIR / "raw"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _parse_iso(s: str):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def embed_missing(hours: int = 72, limit: int = 200) -> int:
    """
    Find videos under /data/raw that have <file>.meta.json but no clip_emb,
    and write clip_emb by sampling frames with ffmpeg + CLIP.
    """
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

        mp = Path(str(p) + ".meta.json")
        if not mp.exists():
            continue

        try:
            meta_txt = mp.read_text(encoding="utf-8")
        except Exception:
            continue

        # quick check: already has clip_emb
        if '"clip_emb"' in meta_txt:
            continue

        # filter by tg_date if present
        try:
            j = json.loads(meta_txt)
        except Exception:
            continue

        tg = (j.get("tg_date") or "").strip()
        if tg:
            dt = _parse_iso(tg)
            if dt and dt < cut:
                continue

        if ensure_meta_clip_emb(str(p)):
            done += 1

    return done


if __name__ == "__main__":
    n = embed_missing(
        hours=int(os.getenv("EMBED_HOURS", "72")),
        limit=int(os.getenv("EMBED_LIMIT", "200")),
    )
    print("EMBEDDED:", n)
