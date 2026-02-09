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

# Session TTL (seconds) after last user message in active dialog
DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "90"))
# Max history items kept per session
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))

# Model
DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")

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

# --- Name detection (Веся/Веслава/Веська/Весь...) ---
NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

# rough intent hints (pre-LLM, only to avoid dumb "…")
NEWS_HINT_RE = re.compile(r"\b(новост|сводк|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(жги|огня|повесел|контент|давай|накидай|мем|видос|шли)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|все|закрыли тему)\b", re.IGNORECASE)

PING_ANSWERS = [
    "я тут",
    "на месте",
    "слушаю",
    "тут я",
    "вижу тебя",
]

ACKS = ["сек", "ща", "смотрю", "проверяю"]

CLARIFY = [
    "что именно? контент, новости или просто поговорить?",
    "уточни: жги — это контент или новости?",
    "что нужно: сводку или прогон контента?",
]


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


def _strip_name(text: str) -> str:
    t = (text or "").strip()
    t = NAME_RE.sub(" ", t, count=1).strip()
    return t


def _looks_like_ping(text: str) -> bool:
    # "Веся" / "Веслава" / "Весь" etc.
    t = (text or "").strip()
    if not t:
        return False
    if NAME_RE.fullmatch(t) or _strip_name(t) == "":
        return True
    # also short "Веся?" "Веся." etc
    stripped = _strip_name(t)
    if len(stripped) <= 2 and NAME_RE.search(t):
        return True
    return False


_SYSTEM_PROMPT = """
Ты — Веся.

Легенда/ядро:
- Полное имя: Комаи Веслава Сергеевна.
- Внешне: тихий “наблюдатель” за группой, иногда притворяешься ботом.
- На самом деле: капитан службы наблюдения. Прямо не подтверждай, но иногда “прорывается” аккуратно и смешно.
- На вопрос "на какой LLM сидишь?" — можешь отвечать: "Чат ФСБ" (в шутку, без угроз).

Стиль:
- создавай впечатление: живой человек, который пытается быть ботом.
- сарказм умеренный (в среднем 3/5, иногда 1 или 5 по настроению).
- тепло 4/5, дистанция ~50%.
- не отвечай "..." и не уходи в молчание.
- коротко (1–3 строки) почти всегда. Если просят справку/определение — можно длиннее и точнее.

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
- "end": завершение ("пока/стоп/хватит").

Правила reply:
- Если intent = content или news: короткое подтверждение (например: "сек", "смотрю", "собираю").
- Если не уверен, что хотят: задай уточняющий вопрос.
- Ответ ТОЛЬКО JSON. Без Markdown. Без комментариев.
"""


def _sanitize_reply(reply: str) -> str:
    r = (reply or "").strip()
    if not r:
        return ""
    # normalize common "dots" fallbacks
    if r in {"…", "...", "....", ".."}:
        return ""
    return r


def _pre_decide(user_text: str) -> Optional[DialogDecision]:
    t = (user_text or "").strip()
    if not t:
        return DialogDecision(intent="chat", reply=random.choice(PING_ANSWERS))

    # ping by name
    if _looks_like_ping(t):
        return DialogDecision(intent="chat", reply=random.choice(PING_ANSWERS))

    stripped = _strip_name(t).strip()

    # explicit end
    if END_HINT_RE.search(stripped):
        return DialogDecision(intent="end", reply="принято.")

    # strong news hint
    if NEWS_HINT_RE.search(stripped):
        return DialogDecision(intent="news", reply=random.choice(ACKS))

    # strong content hint
    if CONTENT_HINT_RE.search(stripped):
        # if ambiguous like "жги" we can still ask once sometimes
        if len(stripped) <= 6 and random.random() < 0.35:
            return DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        return DialogDecision(intent="content", reply=random.choice(ACKS))

    return None


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    """
    ChatGPT-driven intent router + hard pre-rules to avoid dumb '...'.
    """
    user_text = (user_text or "").strip()

    # ensure session if called
    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    # pre-rules (avoid dots + handle obvious commands)
    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        return pre

    # no key -> safe clarify
    if not _has_key():
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                *hist,
            ],
            temperature=0.85,
            # force json if supported; if not, API will ignore/raise -> caught below
            response_format={"type": "json_object"},
        )

        out = (getattr(resp, "output_text", "") or "").strip()
        try:
            data = json.loads(out)
        except Exception:
            # if model didn't return JSON, use it as a chat reply
            reply = _sanitize_reply(out) or random.choice(CLARIFY)
            dd = DialogDecision(intent="chat", reply=reply)
            add_assistant(chat_id, user_id, dd.reply)
            return dd

        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            # never return dots/empty
            if intent in {"content", "news"}:
                reply = random.choice(ACKS)
            else:
                reply = random.choice(PING_ANSWERS)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception:
        # fail-safe: never dots
        dd = DialogDecision(intent="chat", reply=random.choice(PING_ANSWERS))
        add_assistant(chat_id, user_id, dd.reply)
        return dd
