from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

BEAUTY_POOL_PATH = DATA_DIR / "beauty_pool.jsonl"
BEAUTY_SENT_INDEX_PATH = DATA_DIR / "beauty_sent_index.json"


def _load_sent_index() -> Dict[str, List[str]]:
    try:
        if BEAUTY_SENT_INDEX_PATH.exists():
            data = json.loads(BEAUTY_SENT_INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): [str(x) for x in v if x]
                    for k, v in data.items()
                    if isinstance(v, list)
                }
    except Exception:
        pass

    return {}


def _save_sent_index(data: Dict[str, List[str]]) -> None:
    BEAUTY_SENT_INDEX_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_beauty_pool() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    if not BEAUTY_POOL_PATH.exists():
        return rows

    for line in BEAUTY_POOL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue

    return rows


def append_beauty_item(item: Dict[str, Any]) -> bool:
    clip_id = str(item.get("id") or "").strip()
    path = str(item.get("path") or item.get("abs_path") or "").strip()

    if not clip_id or not path:
        return False

    existing_ids = {
        str(x.get("id") or "").strip()
        for x in load_beauty_pool()
        if str(x.get("id") or "").strip()
    }

    if clip_id in existing_ids:
        return False

    item = dict(item)
    item["id"] = clip_id
    item["path"] = path
    item.setdefault("added_ts", int(time.time()))

    with BEAUTY_POOL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return True


def pick_unseen_beauty_clip(user_id: int) -> Optional[Dict[str, Any]]:
    user_key = str(int(user_id))
    sent_index = _load_sent_index()
    seen = set(sent_index.get(user_key) or [])

    candidates = []

    for item in load_beauty_pool():
        clip_id = str(item.get("id") or "").strip()
        path = Path(str(item.get("path") or item.get("abs_path") or ""))

        if not clip_id or clip_id in seen:
            continue

        if not path.exists():
            continue

        candidates.append(item)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            float(x.get("beauty_score") or 0.0),
            float(x.get("erotic_score") or 0.0),
            int(x.get("added_ts") or 0),
        ),
        reverse=True,
    )

    return candidates[0]


def mark_beauty_clip_sent(user_id: int, clip_id: str) -> None:
    user_key = str(int(user_id))
    clip_id = str(clip_id or "").strip()

    if not clip_id:
        return

    sent_index = _load_sent_index()
    current = sent_index.get(user_key) or []

    if clip_id not in current:
        current.append(clip_id)

    if len(current) > 1000:
        current = current[-1000:]

    sent_index[user_key] = current
    _save_sent_index(sent_index)