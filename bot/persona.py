# bot/persona.py
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я))([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_RE = re.compile(r"\b(новост|сводк|че там в мире|что в мире|что происходит|в мире)\b", re.IGNORECASE)
MUSIC_RE = re.compile(r"\b(музык|музон|кавер|cover|что послушать|ютуб|youtube)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(кто жив|есть кто жив|кто тут|живые есть)\b", re.IGNORECASE)
INFO_Q_RE = re.compile(r"\b(что такое|как работает|почему|объясни)\b", re.IGNORECASE)


ACKS = [
    "сек",
    "смотрю",
    "ща гляну",
]

CLARIFY = [
    "что именно?",
    "уточни",
    "про что ты?",
]

ALIVE_ANSWERS = [
    "я здесь",
    "вижу",
    "не сплю",
]

BOT_Q_ANSWERS = [
    "а ты как думаешь?",
    "это важно?",
]

PING_ANSWERS = [
    "я тут",
    "слушаю",
    "да?",
    "что",
    "чем помочь",
]

EXCUSES = [
    "отвлеклась",
    "была занята",
    "пропустила сообщение",
]


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str
    question: str = ""


def is_addressed(text: str) -> bool:
    return bool(NAME_RE.search(text or ""))


def strip_name_prefix(text: str) -> str:
    return NAME_RE.sub(" ", text or "", count=1).strip()


def detect_intent(text: str) -> IntentResult:

    raw = (text or "").strip()
    addressed = is_addressed(raw)

    if not addressed:
        return IntentResult(False, "none")

    t = strip_name_prefix(raw).lower()

    if BOT_Q_ANSWERS and "бот" in t:
        return IntentResult(True, "bot_q")

    if ALIVE_RE.search(t):
        return IntentResult(True, "alive_check")

    if NEWS_RE.search(t):
        return IntentResult(True, "news")

    if MUSIC_RE.search(t):
        return IntentResult(True, "music")

    if "?" in t or INFO_Q_RE.search(t):
        return IntentResult(True, "info_q", t)

    if len(t.strip()) <= 2:
        return IntentResult(True, "ping")

    return IntentResult(True, "unclear")


# ===== delay теперь редкий — 8% =====

def maybe_delay_seconds_for_ping() -> Optional[int]:

    if random.random() < 0.08:
        return random.randint(60, 180)

    return None


def maybe_ack() -> Optional[str]:

    if random.random() < 0.25:
        return random.choice(ACKS)

    return None


def ping_answer() -> str:
    return random.choice(PING_ANSWERS)


def alive_answer() -> str:
    return random.choice(ALIVE_ANSWERS)


def bot_q_answer() -> str:
    return random.choice(BOT_Q_ANSWERS)


def clarify_answer() -> str:
    return random.choice(CLARIFY)


def excuse_text() -> str:
    return random.choice(EXCUSES)


def answer_info_fast(question: str) -> str:

    if not os.getenv("OPENAI_API_KEY"):
        return "сформулируй конкретнее"

    try:

        client = OpenAI()

        resp = client.responses.create(
            model="gpt-5",
            input=f"Ответь коротко и понятно по-русски: {question}",
        )

        return resp.output_text.strip()

    except Exception:

        return "не могу сейчас нормально ответить"
