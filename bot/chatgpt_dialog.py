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
DIALOG_DEBUG = (os.getenv("V_DIALOG_DEBUG", "0") or "").strip() in {"1", "true", "yes", "on"}


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

NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

# FIXED: no \b, matches "Новости" correctly
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


def _log(dd: DialogDecision, where: str, text: str):
    print(f"[dialog] where={where} intent={dd.intent} reply={dd.reply} text={text}", flush=True)


def _now() -> float:
    return time.time()


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
    return NAME_RE.search(t) and len(stripped) <= 2


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

    # HARD RULE: news ALWAYS triggers news
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

    if pre:
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
            input=[
                {"role": "system", "content": "Ответь JSON {intent, reply}"},
                *get_history(chat_id, user_id),
            ],
            temperature=0.8,
        )

        out = getattr(resp, "output_text", "") or str(resp)

        data = json.loads(out)

        intent = data.get("intent", "chat")
        reply = _sanitize_reply(data.get("reply"))

        if not reply:
            reply = random.choice(ACKS)

        dd = DialogDecision(intent, reply)

        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "llm", text)

        return dd

    except Exception as e:
        dd = DialogDecision("chat", random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "except", text)
        return dd
