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


def _dbg(msg: str) -> None:
    if DIALOG_DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[chatgpt_dialog] {now} {msg}", flush=True)


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


@dataclass
class DialogDecision:
    intent: str  # "chat" | "content" | "news" | "end"
    reply: str


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True


_sessions: Dict[Tuple[int, int], _Session] = {}

NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

# HARD RULES:
NEWS_HINT_RE = re.compile(r"\b(новост|сводк|дайджест|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(жги|огня|повесел|контент|накидай|мем|видос|шли)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|все|закрыли тему)\b", re.IGNORECASE)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]
ACKS = ["сек", "ща", "смотрю", "проверяю"]
CLARIFY = [
    "что нужно: контент или новости?",
    "уточни: жги — это контент или новости?",
    "контент, новости или просто поговорить?",
]


def _now() -> float:
    return time.time()


def _log_decision(dd: DialogDecision, where: str, user_text: str) -> None:
    t = (user_text or "").replace("\n", " ").strip()
    if len(t) > 160:
        t = t[:160] + "…"
    r = (dd.reply or "").replace("\n", " ").strip()
    if len(r) > 160:
        r = r[:160] + "…"
    print(f"[dialog] where={where} intent={dd.intent} reply={r} text={t}", flush=True)


def is_active(chat_id: int, user_id: int) -> bool:
    s = _sessions.get((chat_id, user_id))
    if not s or not s.active:
        return False
    return _now() <= s.expires_at


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
    t = (text or "").strip()
    return NAME_RE.sub(" ", t, count=1).strip()


def _looks_like_ping(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if NAME_RE.fullmatch(t) or _strip_name(t) == "":
        return True
    stripped = _strip_name(t)
    return bool(NAME_RE.search(t)) and len(stripped) <= 2


_SYSTEM_PROMPT = """
Ты — Веся.

Легенда/ядро:
- Полное имя: Комаи Веслава Сергеевна.
- Внешне: тихий “наблюдатель” за группой, иногда притворяешься ботом.
- На самом деле: капитан службы наблюдения. Прямо не подтверждай, но иногда “прорывается” аккуратно и смешно.
- На вопрос "на какой LLM сидишь?" — можешь отвечать: "Чат ФСБ" (в шутку, без угроз).

Стиль:
- живой человек, который пытается быть ботом
- сарказм умеренный (в среднем 3/5, иногда 1 или 5)
- тепло 4/5, дистанция ~50%
- не отвечай "..." и не уходи в молчание
- обычно коротко (1–3 строки). По запросу справки — можно длинно и точно.

Формат ответа: СТРОГО JSON:
{
  "intent": "chat" | "content" | "news" | "end",
  "reply": "текст"
}

Смысл intent:
- "content": хотят общий прогон контента (A+B+C)
- "news": хотят новости/сводку
- "chat": разговор
- "end": завершение

Если не уверен — уточни.
Ответ ТОЛЬКО JSON. Без Markdown.
"""


def _sanitize_reply(reply: str) -> str:
    r = (reply or "").strip()
    if not r or r in {"…", "...", "..", "...."}:
        return ""
    return r


def _pre_decide(user_text: str) -> Optional[DialogDecision]:
    t = (user_text or "").strip()
    if not t:
        return DialogDecision(intent="chat", reply=random.choice(PING_ANSWERS))

    if _looks_like_ping(t):
        return DialogDecision(intent="chat", reply=random.choice(PING_ANSWERS))

    stripped = _strip_name(t).strip()

    if END_HINT_RE.search(stripped):
        return DialogDecision(intent="end", reply="принято.")

    # HARD RULE: news always -> intent=news, NO clarify
    if NEWS_HINT_RE.search(stripped):
        return DialogDecision(intent="news", reply=random.choice(ACKS))

    if CONTENT_HINT_RE.search(stripped):
        # only ambiguous for very short "жги/огня"
        if len(stripped) <= 6 and random.random() < 0.35:
            return DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        return DialogDecision(intent="content", reply=random.choice(ACKS))

    return None


def _extract_text(resp: Any) -> str:
    for attr in ("output_text", "text"):
        if hasattr(resp, attr):
            val = getattr(resp, attr)
            if isinstance(val, str) and val.strip():
                return val.strip()

    out = getattr(resp, "output", None)
    if isinstance(out, list) and out:
        chunks: List[str] = []
        for item in out:
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for c in content:
                    txt = getattr(c, "text", None)
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt.strip())
        if chunks:
            return "\n".join(chunks).strip()

    return str(resp).strip()


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    user_text = (user_text or "").strip()

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        _log_decision(pre, "pre", user_text)
        return pre

    if not _has_key():
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        _log_decision(dd, "no_key", user_text)
        return dd

    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
            temperature=0.85,
        )

        out = _extract_text(resp)
        _dbg(f"raw model out: {out[:200].replace(chr(10),' ')}")

        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None
            else:
                data = None

        if not isinstance(data, dict):
            reply = _sanitize_reply(out) or random.choice(CLARIFY)
            dd = DialogDecision(intent="chat", reply=reply)
            add_assistant(chat_id, user_id, dd.reply)
            _log_decision(dd, "bad_json", user_text)
            return dd

        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            reply = random.choice(ACKS) if intent in {"content", "news"} else random.choice(CLARIFY)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        _log_decision(dd, "llm", user_text)
        return dd

    except Exception as e:
        _dbg(f"EXCEPTION: {type(e).__name__}: {e}")
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        _log_decision(dd, "except", user_text)
        return dd
