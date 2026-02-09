# bot/memory.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROFILES_JSON = DATA_DIR / "user_profiles.json"
MEMORY_JSONL = DATA_DIR / "chat_memory.jsonl"

RETENTION_DAYS = int(os.getenv("V_MEMORY_RETENTION_DAYS", "7"))
MAX_EVENTS = int(os.getenv("V_MEMORY_MAX_EVENTS", "1500"))


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


def load_profiles() -> Dict[str, Any]:
    if not PROFILES_JSON.exists():
        return {}
    try:
        return json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_profiles(d: Dict[str, Any]) -> None:
    try:
        PROFILES_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # не валим бота из-за памяти
        pass


def ensure_user_profile(
    profiles: Dict[str, Any],
    *,
    user_id: int,
    display_name: str,
    username: str = "",
) -> Dict[str, Any]:
    uid = str(int(user_id))
    p = profiles.get(uid)
    if not isinstance(p, dict):
        p = {
            "uid": uid,
            "aliases": [],
            "username": "",
            "created_at": _now_utc().isoformat(),
            "last_seen_ts": "",
            "night_owl_score": 0.0,
            "requests": {
                "get12": 0,
                "news": 0,
                "music": 0,
                "alive_check": 0,
                "info_q": 0,
                "unclear": 0,
            },
            "feedback": {
                "good": 0,
                "bad": 0,
                "ban": 0,
            },
        }
        profiles[uid] = p

    # aliases
    dn = (display_name or "").strip()
    if dn:
        aliases = p.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
        if dn not in aliases:
            aliases.append(dn)
            aliases = aliases[-15:]  # держим последние 15
        p["aliases"] = aliases

    un = (username or "").strip()
    if un:
        p["username"] = un

    p["last_seen_ts"] = _now_utc().isoformat()
    return p


def bump_intent(profile: Dict[str, Any], intent: str) -> None:
    req = profile.get("requests")
    if not isinstance(req, dict):
        req = {}
    req[intent] = int(req.get(intent, 0) or 0) + 1
    profile["requests"] = req


def update_night_owl(profile: Dict[str, Any], *, hour_local: int) -> None:
    """
    Простой скоринг "ночной/дневной".
    hour_local — час в локальном времени чата (ты живёшь по Амстердаму, но чат может быть смешанный).
    Мы не пытаемся вычислить timezone; просто поведенческий профиль.
    """
    score = float(profile.get("night_owl_score") or 0.0)
    if hour_local <= 6 or hour_local >= 23:
        score = min(1.0, score + 0.03)
    else:
        score = max(0.0, score - 0.01)
    profile["night_owl_score"] = score


def append_event(event: Dict[str, Any]) -> None:
    try:
        with MEMORY_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def prune_memory(*, retention_days: int = RETENTION_DAYS, max_events: int = MAX_EVENTS) -> None:
    if not MEMORY_JSONL.exists():
        return

    cut = _now_utc() - timedelta(days=max(1, int(retention_days)))

    try:
        lines = MEMORY_JSONL.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    kept: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        ts = _parse_iso(str(obj.get("ts") or ""))
        if ts and ts >= cut:
            kept.append(line)

    # ограничение по количеству
    if len(kept) > max(0, int(max_events)):
        kept = kept[-int(max_events) :]

    try:
        MEMORY_JSONL.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    except Exception:
        pass


def get_recent_events(minutes: int = 30) -> List[Dict[str, Any]]:
    if not MEMORY_JSONL.exists():
        return []
    cut = _now_utc() - timedelta(minutes=max(1, int(minutes)))

    try:
        lines = MEMORY_JSONL.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for line in reversed(lines[-500:]):  # быстро
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        ts = _parse_iso(str(obj.get("ts") or ""))
        if ts and ts >= cut:
            out.append(obj)
        else:
            # дальше будет только старее
            break
    out.reverse()
    return out
