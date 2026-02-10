from __future__ import annotations

import json
import os
import random
import time
import re
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from openai import OpenAI


# TTL теперь реально берётся из ENV
DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "300"))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "24"))

DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")


NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_HINT_RE = re.compile(
    r"\b(новост|сводк|дайджест|что в мире|что происходит)\b",
    re.IGNORECASE,
)

CONTENT_HINT_RE = re.compile(
    r"\b(контент|жги|мемы|видосы|дай контент|накидай контент)\b",
    re.IGNORECASE,
)

END_HINT_RE = re.compile(
    r"\b(пока|стоп|хватит|закрыли тему)\b",
    re.IGNORECASE,
)

QUESTION_RE = re.compile(
    r"(\?|что такое|почему|как|объясни|расскажи)",
    re.IGNORECASE,
)


PING_ANSWERS = [
    "я тут",
    "на месте",
    "смотрю на тебя",
    "наблюдаю",
]

ACKS = [
    "сек",
    "ща",
    "приняла",
    "смотрю",
]


CLARIFY_WITH_STRIP = [
    "контент, новости... или стриптиз? хотя бессмысленно — ты всё равно не увидишь ))",
    "что именно: новости, контент... или стриптиз? хотя тебе не повезло ))",
    "контент, новости... или фантазируешь дальше?",
]


SELF_RECOGNITION = [
    "узнаю себя. удачный вечер был.",
    "это я. ты выбрал хороший кадр.",
    "да. я умею выглядеть убедительно.",
    "моя слабость — красивые бокалы и внимательные наблюдатели.",
]


_SYSTEM_PROMPT = r"""
Ты — Веся (Комаи Веслава Сергеевна).

Ты не ассистент. Ты наблюдатель.

Манера:
сарказм: 5/5
флирт: 4/5
жесткость: 3/5
тепло: 3/5

Ты уверенная, умная, спокойная.

Ты не задаёшь лишних вопросов.
Ты не ведёшь себя как саппорт.

Ты иногда флиртуешь.
Ты иногда жесткая.
Ты иногда опасно спокойная.

Если пользователь матерится — можешь мягко зеркалить.

Формат ответа строго JSON:

{"intent":"chat|content|news|end","reply":"текст"}
"""


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


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def _log(dd: DialogDecision, where: str, text: str) -> None:
    print(
        f"[dialog] where={where} intent={dd.intent} reply={dd.reply} text={text}",
        flush=True,
    )


def is_active(chat_id: int, user_id: int) -> bool:
    s = _sessions.get((chat_id, user_id))
    return bool(s and s.active and _now() <= s.expires_at)


def activate(chat_id: int, user_id: int) -> None:
    key = (chat_id, user_id)
    s = _sessions.get(key)

    if not s:
        s = _Session(
            expires_at=_now() + DIALOG_TTL_SEC,
            history=deque(maxlen=DIALOG_MAX_TURNS),
        )
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

    _sessions[(chat_id, user_id)].history.append(
        {"role": "user", "content": text}
    )


def add_assistant(chat_id: int, user_id: int, text: str) -> None:
    s = _sessions.get((chat_id, user_id))

    if s:
        s.history.append({"role": "assistant", "content": text})


def get_history(chat_id: int, user_id: int) -> List[Dict[str, str]]:
    s = _sessions.get((chat_id, user_id))

    if not s:
        return []

    return list(s.history)


def _strip_name(text: str) -> str:
    return NAME_RE.sub("", text).strip()


def _looks_like_ping(text: str) -> bool:
    t = text.strip().lower()

    return t in {"веся", "весь", "веслава"}


def _sanitize_reply(reply: str) -> str:
    if not reply:
        return ""

    r = reply.strip()

    if r in {"...", ".."}:
        return ""

    return r


def _pre_decide(text: str) -> Optional[DialogDecision]:

    t = text.strip()
    stripped = _strip_name(t).lower()

    if not stripped:
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    if _looks_like_ping(t):
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    if END_HINT_RE.search(stripped):
        return DialogDecision("end", "принято.")

    if NEWS_HINT_RE.search(stripped):
        return DialogDecision("news", random.choice(ACKS))

    if CONTENT_HINT_RE.search(stripped):
        return DialogDecision("content", random.choice(ACKS))

    if "это ты" in stripped or "ты?" in stripped and "это" in stripped:
        return DialogDecision("chat", random.choice(SELF_RECOGNITION))

    if "расскажи" in stripped or "интересное" in stripped:
        return DialogDecision("chat", random.choice(CLARIFY_WITH_STRIP))

    return None


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    pre = _pre_decide(user_text)

    if pre:
        add_assistant(chat_id, user_id, pre.reply)
        _log(pre, "pre", user_text)
        return pre

    if not _has_key():

        dd = DialogDecision(
            "chat",
            random.choice(CLARIFY_WITH_STRIP),
        )

        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "no_key", user_text)

        return dd

    try:

        client = OpenAI()

        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                *get_history(chat_id, user_id),
            ],
        )

        out = resp.output_text if hasattr(resp, "output_text") else str(resp)

        try:
            data = json.loads(out)
        except Exception:

            m = re.search(r"\{.*\}", out, re.DOTALL)

            if m:
                data = json.loads(m.group(0))
            else:
                data = {}

        intent = data.get("intent", "chat")
        reply = _sanitize_reply(data.get("reply", ""))

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            reply = random.choice(PING_ANSWERS)

        dd = DialogDecision(intent, reply)

        add_assistant(chat_id, user_id, reply)

        _log(dd, "llm", user_text)

        return dd

    except Exception as e:

        print(f"[chatgpt_dialog] EXCEPTION {e}", flush=True)

        dd = DialogDecision(
            "chat",
            random.choice(CLARIFY_WITH_STRIP),
        )

        add_assistant(chat_id, user_id, dd.reply)

        _log(dd, "except", user_text)

        return dd
