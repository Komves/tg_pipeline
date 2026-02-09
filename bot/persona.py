# bot/persona.py
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я))([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_RE = re.compile(r"\b(новост|че там|что в мире)\b", re.IGNORECASE)
MUSIC_RE = re.compile(r"\b(музык|музон|ютуб|youtube)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(кто жив|живые есть|ты тут)\b", re.IGNORECASE)
BOT_RE = re.compile(r"\b(ты бот)\b", re.IGNORECASE)

INFO_RE = re.compile(
    r"\b(что такое|как работает|почему|объясни|где ты|как дела|как сама|чем помочь)\b",
    re.IGNORECASE,
)


PING_ANSWERS = [
    "я тут",
    "слушаю",
    "да",
]

CHAT_ANSWERS = [
    "нормально",
    "в порядке",
    "живу",
    "работаю",
    "смотрю",
]

CLARIFY = [
    "что?",
    "не поняла",
    "уточни",
]

ALIVE = [
    "я здесь",
    "вижу",
]

EXCUSES = [
    "отвлеклась",
    "была занята",
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

    if not is_addressed(text):
        return IntentResult(False, "none")

    t = strip_name_prefix(text).lower()

    if t == "":
        return IntentResult(True, "ping")

    if BOT_RE.search(t):
        return IntentResult(True, "bot_q")

    if ALIVE_RE.search(t):
        return IntentResult(True, "alive")

    if NEWS_RE.search(t):
        return IntentResult(True, "news")

    if MUSIC_RE.search(t):
        return IntentResult(True, "music")

    if INFO_RE.search(t):
        return IntentResult(True, "chat", t)

    if "?" in t:
        return IntentResult(True, "chat", t)

    return IntentResult(True, "chat", t)


# ===== BEHAVIOR =====

def maybe_delay_seconds_for_ping() -> Optional[int]:

    if random.random() < 0.05:
        return random.randint(30, 120)

    return None


def ping_answer() -> str:
    return random.choice(PING_ANSWERS)


def chat_answer() -> str:
    return random.choice(CHAT_ANSWERS)


def alive_answer() -> str:
    return random.choice(ALIVE)


def clarify_answer() -> str:
    return random.choice(CLARIFY)


def excuse_text() -> str:
    return random.choice(EXCUSES)


def bot_q_answer() -> str:
    return "возможно"


def answer_info_fast(question: str) -> str:

    if not os.getenv("OPENAI_API_KEY"):
        return chat_answer()

    try:

        client = OpenAI()

        r = client.responses.create(
            model="gpt-5",
            input=f"Ответь коротко по-русски: {question}",
        )

        return r.output_text.strip()

    except Exception:

        return chat_answer()
