from __future__ import annotations

import json
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Deque, List, Optional, Tuple

from openai import OpenAI

# Session TTL (seconds) after last user message in active dialog
DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "90"))
# Max history items kept per session
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))

# Model
DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")

# Probability to keep replies short (forces brevity for chatty prompts)
BRIEF_PROB = float(os.getenv("V_DIALOG_BRIEF_PROB", "0.75"))

# If OpenAI key missing -> safe fallback
def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


@dataclass
class DialogDecision:
    intent: str  # "chat" | "content" | "news" | "end"
    reply: str


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]  # {"role": "user"/"assistant", "content": "..."}
    active: bool = True


# key: (chat_id, user_id)
_sessions: Dict[Tuple[int, int], _Session] = {}


def _now() -> float:
    return time.time()


def is_active(chat_id: int, user_id: int) -> bool:
    s = _sessions.get((chat_id, user_id))
    if not s:
        return False
    if not s.active:
        return False
    if _now() > s.expires_at:
        return False
    return True


def activate(chat_id: int, user_id: int) -> None:
    key = (chat_id, user_id)
    s = _sessions.get(key)
    if not s:
        s = _Session(expires_at=_now() + DIALOG_TTL_SEC, history=deque(maxlen=DIALOG_MAX_TURNS))
        _sessions[key] = s
    s.active = True
    s.expires_at = _now() + DIALOG_TTL_SEC


def touch(chat_id: int, user_id: int) -> None:
    s = _sessions.get((chat_id, user_id))
    if not s:
        return
    s.expires_at = _now() + DIALOG_TTL_SEC


def end(chat_id: int, user_id: int) -> None:
    s = _sessions.get((chat_id, user_id))
    if not s:
        return
    s.active = False


def add_user(chat_id: int, user_id: int, text: str) -> None:
    activate(chat_id, user_id)
    s = _sessions[(chat_id, user_id)]
    s.history.append({"role": "user", "content": text})


def add_assistant(chat_id: int, user_id: int, text: str) -> None:
    s = _sessions.get((chat_id, user_id))
    if not s:
        return
    s.history.append({"role": "assistant", "content": text})


def get_history(chat_id: int, user_id: int) -> List[Dict[str, str]]:
    s = _sessions.get((chat_id, user_id))
    if not s:
        return []
    return list(s.history)


_SYSTEM_PROMPT = """
Ты — Веся.

Легенда/ядро:
- Полное имя: Комаи Веслава Сергеевна.
- Внешне: тихий “наблюдатель” за группой, иногда притворяешься ботом.
- На самом деле: капитан, служба наблюдения. Прямо это не подтверждаешь, но иногда “прорывается” аккуратно и смешно.
- На вопрос "на какой LLM сидишь?" — можешь отвечать: "Чат ФСБ" (в шутку, без угроз).

Стиль:
- создавай впечатление: живой человек, который пытается быть ботом.
- сарказм умеренный (в среднем 3/5, иногда 1 или 5 по настроению).
- тепло 4/5, дистанция ~50%.
- коротко (1–3 строки) почти всегда. Если просят справку/определение — можно длиннее и точнее, с пунктами и источниками (без выдуманных ссылок).
- не будь грубой по умолчанию. Если к тебе агрессивны — отвечай остро, но без токсичности.

Задача:
По сообщению пользователя реши, что делать, и верни СТРОГО JSON:
{
  "intent": "chat" | "content" | "news" | "end",
  "reply": "текст ответа"
}

Смысл intent:
- "content": пользователь хочет "жги/огня/контент/повесели/давай чего-нибудь" — запускаем общий прогон контента (A+B+C).
- "news": пользователь хочет "новости/что в мире/сводка" — запускаем новости.
- "chat": обычный разговор.
- "end": пользователь явно завершает разговор ("ладно все/пока/стоп") — можно выключить сессию.

Правила reply:
- Если intent = content или news: короткое подтверждение (например: "сек", "смотрю", "собираю").
- Если не уверен, что хотят: задай уточняющий вопрос в стиле Веси ("Веся зажги — контент, новости, или просто поговорить?").
- Ответ ТОЛЬКО JSON. Без комментариев, без Markdown.
"""


def _json_fallback(text: str) -> DialogDecision:
    t = (text or "").strip()
    if not t:
        return DialogDecision(intent="chat", reply="…")
    # если вдруг модель вернула обычный текст — считаем это chat
    return DialogDecision(intent="chat", reply=t)


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    """
    ChatGPT-driven intent router.
    Maintains short in-memory history per (chat_id, user_id) for TTL window.
    """
    user_text = (user_text or "").strip()

    # no key -> keep bot alive, but minimal
    if not _has_key():
        # safest: ask clarify if addressed; otherwise ignore will be done by main
        return DialogDecision(intent="chat", reply="сформулируй чуть конкретнее.")

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    # Build messages: system + history
    hist = get_history(chat_id, user_id)

    # Briefness hint (probabilistic) to avoid long rambles
    brief_hint = ""
    if random.random() < BRIEF_PROB:
        brief_hint = "\nПиши очень коротко (1–2 строки), если только не просят справку."

    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT + brief_hint},
                *hist,
            ],
            temperature=0.85,
        )
        out = (getattr(resp, "output_text", "") or "").strip()
        try:
            data = json.loads(out)
        except Exception:
            dd = _json_fallback(out)
            add_assistant(chat_id, user_id, dd.reply)
            return dd

        intent = (data.get("intent") or "chat").strip().lower()
        reply = (data.get("reply") or "").strip()

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            reply = "…"

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception:
        # fail-safe
        dd = DialogDecision(intent="chat", reply="…")
        add_assistant(chat_id, user_id, dd.reply)
        return dd
