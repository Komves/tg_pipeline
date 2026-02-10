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

DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "90"))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))
DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")

NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_HINT_RE = re.compile(r"\b(новост|сводк|дайджест|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(контент|жги|огня|накидай|мем|мемы|видос|видосы|шли|покидай)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|всё|все|закрыли тему)\b", re.IGNORECASE)

QUESTION_RE = re.compile(
    r"(\?|что такое|что значит|объясни|расскажи|почему|зачем|как устроен|как работает|чем отличается)",
    re.IGNORECASE,
)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]
ACKS = ["сек", "ща", "смотрю", "проверяю"]
CLARIFY = [
    "что нужно: контент или новости?",
    "уточни: контент или новости?",
    "контент или новости?",
]

_SYSTEM_PROMPT = """
Ты — Веся (Комаи Веслава Сергеевна).

Легенда:
- Снаружи: тихий наблюдатель за группой, иногда притворяешься ботом.
- Внутри: капитан службы наблюдения. Прямо не подтверждай, но иногда прорывается смешной намёк.
- На вопрос "на какой LLM сидишь?" можешь шутить: "Чат ФСБ" (без угроз).

Стиль:
- живой человек, который пытается быть ботом
- сарказм умеренный (3/5), тепло 4/5, дистанция ~50%
- обычно 1–3 строки
- не отвечай "..." и не молчи

Формат ответа: СТРОГО JSON:
{"intent":"chat|content|news|end","reply":"текст"}

Правила интента:
- Если явный запрос новостей/сводки — intent="news".
- Если явный запрос контента/жги/мемы/видосы — intent="content".
- Если вопрос/объяснение — intent="chat".
- Если "пока/стоп" — intent="end".
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
    t = (text or "").replace("\n", " ").strip()
    if len(t) > 160:
        t = t[:160] + "…"
    r = (dd.reply or "").replace("\n", " ").strip()
    if len(r) > 160:
        r = r[:160] + "…"
    print(f"[dialog] where={where} intent={dd.intent} reply={r} text={t}", flush=True)


def _log_exc(e: Exception) -> None:
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


def get_history(chat_id: int, user_id: int) -> List[Dict[str, str]]:
    s = _sessions.get((chat_id, user_id))
    return list(s.history) if s else []


def _strip_name(text: str) -> str:
    return NAME_RE.sub(" ", (text or "").strip(), count=1).strip()


def _looks_like_ping(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if NAME_RE.fullmatch(t) or _strip_name(t) == "":
        return True
    stripped = _strip_name(t)
    return bool(NAME_RE.search(t)) and len(stripped) <= 2


def _sanitize_reply(reply: str) -> str:
    r = (reply or "").strip()
    if r in {"", "...", "..", "…"}:
        return ""
    return r


def _pre_decide(text: str) -> Optional[DialogDecision]:
    t = (text or "").strip()
    if not t:
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    if _looks_like_ping(t):
        return DialogDecision("chat", random.choice(PING_ANSWERS))

    stripped = _strip_name(t)

    if END_HINT_RE.search(stripped):
        return DialogDecision("end", "принято.")

    # ЖЁСТКО: новости/контент определяем правилами, без LLM-уточнений
    if NEWS_HINT_RE.search(stripped):
        return DialogDecision("news", random.choice(ACKS))

    if CONTENT_HINT_RE.search(stripped):
        return DialogDecision("content", random.choice(ACKS))

    # Если похоже на вопрос — это разговор через LLM
    if QUESTION_RE.search(stripped):
        return None

    return None


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    user_text = (user_text or "").strip()

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        _log(pre, "pre", user_text)
        return pre

    if not _has_key():
        dd = DialogDecision("chat", "ключа OpenAI не вижу. могу: новости или контент.")
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "no_key", user_text)
        return dd

    try:
        client = OpenAI()
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *get_history(chat_id, user_id)],
        )

        out = getattr(resp, "output_text", None) or getattr(resp, "text", None)
        if not isinstance(out, str) or not out.strip():
            out = str(resp)

        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}

        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"
        if not reply:
            reply = "поняла. продолжай."

        # НО: если LLM вдруг вернёт news/content — не даём ему начинать интервью,
        # reply должен быть короткий ACK.
        if intent in {"news", "content"}:
            reply = random.choice(ACKS)

        dd = DialogDecision(intent, reply)
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "llm", user_text)
        return dd

    except Exception as e:
        _log_exc(e)
        dd = DialogDecision("chat", "я тут. скажи: новости, контент или задавай вопрос 🙂")
        add_assistant(chat_id, user_id, dd.reply)
        _log(dd, "except", user_text)
        return dd
