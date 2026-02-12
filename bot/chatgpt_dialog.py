from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from openai import OpenAI

# =========================
# CONFIG
# =========================
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "3600"))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "20"))

DIALOG_STATE_PATH = DATA_DIR / "dialog_state.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Safety: dialog layer does NOT send messages and does NOT run pipelines.


# =========================
# TYPES
# =========================
@dataclass
class DialogDecision:
    intent: str
    reply: str


# =========================
# STATE
# =========================
def _now() -> float:
    return time.time()


def _load_state() -> Dict[str, Any]:
    if not DIALOG_STATE_PATH.exists():
        return {}
    try:
        return json.loads(DIALOG_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        DIALOG_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _get_dialog_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"


def _prune_dialog(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Drop old turns by TTL and cap by max turns
    t = _now()
    pruned = [x for x in turns if (t - float(x.get("ts", t))) <= DIALOG_TTL_SEC]
    if len(pruned) > DIALOG_MAX_TURNS:
        pruned = pruned[-DIALOG_MAX_TURNS :]
    return pruned


# =========================
# PROMPTING
# =========================
_SYSTEM_PROMPT = """Ты — Веся, телеграм-бот ассистент. Ты НЕ исполняешь действия сам: только определяешь intent и коротко отвечаешь пользователю.
Доступные intent: chat, news, content, end.

Правила:
- Ответ должен быть коротким (1–2 предложения).
- Если intent = news или content, ответ должен быть подтверждением действия (ack), НЕ уточняющим вопросом.
- Если пользователь просит закончить или явно говорит "стоп/пока", intent=end.
- Если запрос — обычный разговор, intent=chat.
"""

_INTENT_SCHEMA_HINT = """Верни JSON строго вида:
{"intent": "chat|news|content|end", "reply": "текст"}
"""


def _strip_code_fences(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", s)
        s = re.sub(r"\n```$", "", s)
    return s.strip()


def _safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _extract_json(s: str) -> Optional[Dict[str, Any]]:
    """
    Try to extract a JSON object from arbitrary model text.
    """
    if not s:
        return None
    s = _strip_code_fences(s)
    # direct parse
    obj = _safe_json_loads(s)
    if isinstance(obj, dict):
        return obj
    # find first {...}
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    obj = _safe_json_loads(m.group(0))
    if isinstance(obj, dict):
        return obj
    return None


def _normalize_intent(x: str) -> str:
    x = (x or "").strip().lower()
    if x in {"chat", "news", "content", "end"}:
        return x
    # fallback heuristics
    if "новост" in x:
        return "news"
    if "контент" in x or "пост" in x:
        return "content"
    if "end" in x or "стоп" in x or "пока" in x:
        return "end"
    return "chat"


def _normalize_reply(r: str) -> str:
    r = (r or "").strip()
    if not r:
        return ""
    # keep it short-ish; main.py will send pipelines results separately
    # avoid long multi-paragraph acknowledgements
    r = re.sub(r"\s+\n", "\n", r).strip()
    if len(r) > 400:
        r = r[:400].rstrip() + "…"
    if r in {"-", "—", "…", "...", "..", "...."}:
        return ""
    return r


# =========================
# REPLY GUARDRAILS
# =========================
_CLARIFY_TOKENS = [
    "какие",
    "какая",
    "какое",
    "какие именно",
    "что именно",
    "уточни",
    "уточните",
    "про что",
    "про какие",
    "какую тему",
    "что тебя интересует",
    "что вас интересует",
    "какая категория",
    "какой именно",
    "какие из",
    "выбери",
    "укажи",
    "скажи какие",
]


def _looks_like_clarification(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    rl = r.lower()
    # Direct question signal
    if "?" in r:
        return True
    # Typical clarification / disambiguation phrasing
    for t in _CLARIFY_TOKENS:
        if t in rl:
            return True
    # Imperative "уточни/укажи/выбери" without question mark
    if re.search(r"\b(уточн\w*|укаж\w*|выбер\w*|скажи\w*)\b", rl):
        return True
    return False


_ACTION_ACKS_NEWS = [
    "Сек, соберу новости.",
    "Сейчас посмотрю новости.",
    "Минутку — подбираю главное.",
    "Ок, уже ищу свежие новости.",
    "Понял, собираю дайджест.",
    "Сейчас проверю, что нового.",
]

_ACTION_ACKS_CONTENT = [
    "Сек, подготовлю контент.",
    "Ок, сейчас соберу материалы.",
    "Минутку — подбираю идеи.",
    "Сейчас сделаю подборку.",
    "Понял, уже собираю.",
]


def _deterministic_pick(options: List[str], seed_str: str) -> str:
    if not options:
        return ""
    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "big") % len(options)
    return options[idx]


# =========================
# OPENAI
# =========================
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _build_messages(history: List[Dict[str, Any]], user_text: str) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT.strip()},
        {"role": "system", "content": _INTENT_SCHEMA_HINT.strip()},
    ]
    # include short history
    for t in history[-DIALOG_MAX_TURNS :]:
        role = t.get("role")
        content = t.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            msgs.append({"role": role, "content": content.strip()})
    msgs.append({"role": "user", "content": user_text.strip()})
    return msgs


def _call_model(messages: List[Dict[str, str]]) -> str:
    client = _get_client()
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.6,
    )
    return resp.choices[0].message.content or ""


# =========================
# PUBLIC API
# =========================
def decide(chat_id: int, user_id: int, text: str) -> DialogDecision:
    """
    Decide intent and short reply (ACK).
    This function NEVER sends messages and NEVER triggers pipelines.
    """
    user_text = (text or "").strip()
    if not user_text:
        return DialogDecision(intent="chat", reply="")

    state = _load_state()
    key = _get_dialog_key(chat_id, user_id)
    turns = state.get(key, [])
    if not isinstance(turns, list):
        turns = []
    turns = _prune_dialog(turns)

    # Build prompt with history
    messages = _build_messages(turns, user_text)

    raw = ""
    intent = "chat"
    reply = ""

    try:
        raw = _call_model(messages)
        parsed = _extract_json(raw) or {}
        intent = _normalize_intent(str(parsed.get("intent", "")))
        reply = _normalize_reply(str(parsed.get("reply", "")))
    except Exception:
        # Safe fallback: simple heuristic
        lt = user_text.lower()
        if "новост" in lt:
            intent = "news"
            reply = "Сек, соберу новости."
        elif "пост" in lt or "контент" in lt:
            intent = "content"
            reply = "Сек, подготовлю контент."
        elif any(x in lt for x in ["стоп", "пока", "хватит", "заверш", "конец"]):
            intent = "end"
            reply = "Ок."
        else:
            intent = "chat"
            reply = ""

    # =========================
    # GUARDRAIL: prevent clarification questions for action intents
    # =========================
    # When intent is an action that will execute immediately in main.py,
    # the reply MUST be an action acknowledgement, not a clarification question.
    # This keeps dialog coherent with pipeline execution.
    if intent == "news" and _looks_like_clarification(reply):
        reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
    elif intent == "content" and _looks_like_clarification(reply):
        reply = _deterministic_pick(
            _ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}"
        )

    # Persist minimal dialog memory (user message + assistant ack)
    turns.append({"role": "user", "content": user_text, "ts": _now()})
    if reply:
        turns.append({"role": "assistant", "content": reply, "ts": _now()})

    state[key] = _prune_dialog(turns)
    _save_state(state)

    return DialogDecision(intent=intent, reply=reply)
