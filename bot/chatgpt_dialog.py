from __future__ import annotations

import json
import os
import random
import time
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Deque, List, Optional, Tuple

from openai import OpenAI

DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "90"))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))
DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")

NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)
NEWS_HINT_RE = re.compile(r"(новост|сводк|дайджест|что в мире|че там в мире|что происходит|в мире)", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"(жги|огня|повесел|контент|накидай|мем|видос|шли)", re.IGNORECASE)
END_HINT_RE = re.compile(r"(пока|стоп|хватит|все|закрыли тему)", re.IGNORECASE)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]
ACKS = ["сек", "ща", "смотрю", "проверяю"]
CLARIFY = [
    "что нужно: контент или новости?",
    "уточни: жги — это контент или новости?",
    "контент, новости или просто поговорить?",
]

_SYSTEM_PROMPT = """
Ты — Веся.
Ответ строго JSON:
{"intent":"chat|content|news|end","reply":"текст"}
"""


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


@dataclass
class DialogDecision:
    intent: str
    reply: str


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True


_sessions: Dict[Tuple[int, int], _Session] = {}


def _now() -> float:
    return time.time()


def _log(dd: DialogDecision, where: str, text: str):
    t = (text or "").replace("\n", " ").strip()
    if len(t) > 160:
        t = t[:160] + "…"
    r = (dd.reply or "").replace("\n", " ").strip()
    if len(r) > 160:
        r = r[:160] + "…"
    print(f"[dialog] where={where} intent={dd.intent} reply={r} text={t}", flush=True)


def _log_exc(e: Exception):
    print(f"[chatgpt_dialog] EXCEPTION {type(e).__name__}: {e}", flush=True)


def is_active(chat_id: int, user_id: int) -> bool:
    s = _sessions.get((chat_id, user_id))
    return bool(s and s.active and _now() <= s.expires_at)


def activate(chat_id: int, user_id: int) -> None:
    key = (chat_id, user_id)
    s = _sessions.get(key)
    if not s:
        s = _Session(_now() + DIALOG_TTL_SEC, deque(maxlen=DIALOG_MAX_TURNS))
        _sessions[key] = s
    s.active = True
    s.expires_at = _now() + DIALOG_TTL_SEC


def touch(chat_id: int, user_id: int) -> None:
    s = _sessions.get((chat_id, user_id))
    if s:
        s.expires_at = _now() + DIALOG_TTL_SEC


def end(chat_id: int, user_id: int) -> None:
    s = _sessions.get((chat_id, user_id))
    if s:
        s.active = False


def add_user(chat_id: int, user_id: int, text: str) -> None:
    activate(chat_id, user_id)
    _sessions[(chat_id, user_id)].history.append({"role": "user", "content": text})


def add_assistant(chat_id: int, user_id: int, text: str) -> None:
    s = _sessions.get((chat_id, user_id))
    if s:
        s.history.append({"role": "assistant", "content": text})


def get_history(chat_id: int, user_id: int):
    s = _sessions.get((chat_id, user_id))
    return list(s.history) if s else []


def _strip_name(text: str) -> str:
    return NAME_RE.sub(" ", text or "", count=1).strip()


def _looks_like_ping(text: str) -> bool:
    t = (text or "").strip()
    if NAME_RE.fullmatch(t):
        return True
    stripped = _strip_name(t)
    return bool(NAME_RE.search(t)) and len(stripped) <= 2


def _sanitize_reply(reply: str) -> str:
    reply = (reply or "").strip()
    if reply in {"", "...", "..", "…"}:
        return ""
    return reply


def _pre_decide(text: str) -> Optional[DialogDecision]:
    t = (text or "").strip()
    if not t:
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    if _looks_like_ping(t):
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    stripped = _strip_name(t)

    if END_HINT_RE.search(stripped):
        return DialogDecision("end", "принято.")

    if NEWS_HINT_RE.search(stripped):
        return DialogDecision("news", random.choice(ACKS))

    if CONTENT_HINT_RE.search(stripped):
        if len(stripped) <= 6:
            return DialogDecision("chat", random.choice(CLARIFY))
        return DialogDecision("content", random.choice(ACKS))

    return None


def decide(chat_id: int, user_id: int, text: str) -> DialogDecision:
    text = (text or "").strip()
    add_user(chat_id, user_id, text)
    touch(chat_id, user_id)

    pre = _pre_decide(text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        _log(pre, "pre", text)
        return pre

    if not _has_key():
        dd = DialogDecision("chat", random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "no_key", text)
        return dd

    try:
        client = OpenAI()
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *get_history(chat_id, user_id)],
            temperature=0.7,
        )

        out = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""
        if not isinstance(out, str) or not out.strip():
            out = str(resp)

        data = json.loads(out)
        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"
        if not reply:
            reply = random.choice(CLARIFY)

        dd = DialogDecision(intent, reply)
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "llm", text)
        return dd

    except Exception as e:
        _log_exc(e)
        dd = DialogDecision("chat", random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "except", text)
        return dd
