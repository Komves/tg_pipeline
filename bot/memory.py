# bot/memory.py
from __future__ import annotations

import json
import os
import re
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


def get_recent_events_for_chat(chat_id: int, limit: int = 300) -> List[Dict[str, Any]]:
    if not MEMORY_JSONL.exists():
        return []

    try:
        lines = MEMORY_JSONL.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []

    for line in reversed(lines[-2000:]):
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except Exception:
            continue

        try:
            if int(obj.get("chat_id") or 0) != int(chat_id):
                continue
        except Exception:
            continue

        out.append(obj)

        if len(out) >= int(limit):
            break

    out.reverse()
    return out


def render_reply_thread_context(
    *,
    chat_id: int,
    reply_to_message_id: int,
    limit: int = 6,
) -> str:
    events = get_recent_events_for_chat(chat_id, limit=500)

    by_msg: Dict[int, Dict[str, Any]] = {}

    for e in events:
        try:
            mid = int(e.get("message_id") or 0)
        except Exception:
            mid = 0

        if mid:
            by_msg[mid] = e

    chain: List[Dict[str, Any]] = []
    current_id = int(reply_to_message_id or 0)
    visited = set()

    while current_id and current_id not in visited and len(chain) < int(limit):
        visited.add(current_id)

        e = by_msg.get(current_id)
        if not e:
            break

        chain.append(e)

        try:
            current_id = int(e.get("reply_to_message_id") or 0)
        except Exception:
            current_id = 0

    chain.reverse()

    if not chain:
        return ""

    lines = ["Контекст reply-ветки. Используй как главный контекст текущего ответа:"]

    for e in chain:
        user_name = str(e.get("user_name") or "").strip() or "участник"
        text = str(e.get("text") or "").replace("\n", " ").strip()
        reply = str(e.get("reply") or "").replace("\n", " ").strip()

        if text:
            lines.append(f"- {user_name}: {text[:500]}")

        if reply:
            lines.append(f"- Веся: {reply[:500]}")

    return "\n".join(lines).strip()


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

def _ensure_facts(profile: Dict[str, Any]) -> Dict[str, Any]:
    facts = profile.get("facts")
    if not isinstance(facts, dict):
        facts = {}

    for k in ("preferences", "music_preferences", "dislikes", "notes"):
        if not isinstance(facts.get(k), list):
            facts[k] = []

    profile["facts"] = facts
    return facts


def _append_unique(xs: List[str], value: str, *, max_items: int = 50) -> None:
    v = (value or "").strip(" .,:;—-\n\t").strip()
    if not v:
        return

    low = v.lower()
    for x in xs:
        if str(x).lower() == low:
            return

    xs.append(v)
    if len(xs) > max_items:
        del xs[:-max_items]


def update_user_facts_from_text(profile: Dict[str, Any], text: str) -> bool:
    """
    Deterministic semantic facts extractor.
    No LLM call here: memory must be cheap and stable.
    """
    t = (text or "").strip()
    if not t:
        return False

    tl = t.lower()
    facts = _ensure_facts(profile)
    changed = False

    # Strip Vesya address/prefix.
    clean = re.sub(
        r"^\s*(веся|веська|веслава|vesya|сергеевна)\s*[,.:;!\-]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()

    # Explicit remember/note.
    m = re.search(r"\b(запомни|запиши|сохрани|учти)\b\s*[:\-]?\s*(.+)$", clean, flags=re.I)
    if m:
        note = m.group(2).strip()
        if note:
            _append_unique(facts["notes"], note)
            changed = True

    # Likes / preferences.
    like_patterns = [
        r"\bя\s+люблю\s+(.+)$",
        r"\bмне\s+нрав(?:ится|ятся)\s+(.+)$",
        r"\bмне\s+заход(?:ит|ят)\s+(.+)$",
        r"\bпредпочитаю\s+(.+)$",
    ]

    for pat in like_patterns:
        m = re.search(pat, clean, flags=re.I)
        if not m:
            continue

        raw = m.group(1).strip()
        parts = re.split(r"\s*(?:,|;|\s+и\s+|\s+или\s+)\s*", raw)

        is_music = bool(re.search(
            r"\b(metal|doom|rock|cover|кавер|каверы|музык|трек|песня|жанр|метал|рок)\b",
            raw,
            flags=re.I,
        ))

        for p in parts:
            if not p:
                continue
            if is_music:
                _append_unique(facts["music_preferences"], p)
            else:
                _append_unique(facts["preferences"], p)
            changed = True

    # Dislikes / irritants.
    dislike_patterns = [
        r"\bя\s+не\s+люблю\s+(.+)$",
        r"\bмне\s+не\s+нрав(?:ится|ятся)\s+(.+)$",
        r"\bменя\s+бес(?:ит|ят)\s+(.+)$",
        r"\bненавижу\s+(.+)$",
    ]

    for pat in dislike_patterns:
        m = re.search(pat, clean, flags=re.I)
        if not m:
            continue

        raw = m.group(1).strip()
        parts = re.split(r"\s*(?:,|;|\s+и\s+|\s+или\s+)\s*", raw)

        for p in parts:
            if not p:
                continue
            _append_unique(facts["dislikes"], p)
            changed = True

    if changed:
        profile["facts_updated_at"] = _now_utc().isoformat()

    return changed


def render_profile_facts_context(profile: Dict[str, Any]) -> str:
    facts = profile.get("facts")
    if not isinstance(facts, dict):
        return ""

    lines: List[str] = []

    prefs = [str(x) for x in facts.get("preferences") or [] if str(x).strip()]
    music = [str(x) for x in facts.get("music_preferences") or [] if str(x).strip()]
    dislikes = [str(x) for x in facts.get("dislikes") or [] if str(x).strip()]
    notes = [str(x) for x in facts.get("notes") or [] if str(x).strip()]

    if music:
        lines.append("Музыкальные предпочтения пользователя: " + "; ".join(music[-12:]))
    if prefs:
        lines.append("Предпочтения пользователя: " + "; ".join(prefs[-12:]))
    if dislikes:
        lines.append("Не любит / раздражает: " + "; ".join(dislikes[-12:]))
    if notes:
        lines.append("Заметки, которые пользователь просил помнить: " + "; ".join(notes[-12:]))

    if not lines:
        return ""

    return (
        "Устойчивые факты о пользователе. "
        "Используй как контекст, не пересказывай без необходимости:\n"
        + "\n".join(f"- {x}" for x in lines)
    )


def render_user_profile_context(*, user_id: int) -> str:
    profiles = load_profiles()
    profile = profiles.get(str(int(user_id)))
    if not isinstance(profile, dict):
        return ""
    return render_profile_facts_context(profile)
