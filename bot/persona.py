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
MUSIC_RE = re.compile(r"\b(музык|youtube|ютуб)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(жив|ты тут)\b", re.IGNORECASE)
BOT_RE = re.compile(r"\b(ты бот)\b", re.IGNORECASE)


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str
    question: str = ""


def is_addressed(text: str) -> bool:
    return bool(NAME_RE.search(text or ""))


def strip_name_prefix(text: str) -> str:
    return NAME_RE.sub("", text or "", count=1).strip()


def detect_intent(text: str) -> IntentResult:

    if not is_addressed(text):
        return IntentResult(False, "none")

    t = strip_name_prefix(text)

    if not t:
        return IntentResult(True, "ping")

    if BOT_RE.search(t):
        return IntentResult(True, "bot_q")

    if NEWS_RE.search(t):
        return IntentResult(True, "news")

    if MUSIC_RE.search(t):
        return IntentResult(True, "music")

    if ALIVE_RE.search(t):
        return IntentResult(True, "chat", t)

    return IntentResult(True, "chat", t)


# ===== personality =====

PING = [
    "я тут",
    "да",
    "слушаю",
]

EXCUSES = [
    "отвлеклась",
    "была занята",
]


def maybe_delay_seconds_for_ping() -> Optional[int]:

    if random.random() < 0.05:
        return random.randint(30, 120)

    return None


def ping_answer() -> str:
    return random.choice(PING)


def excuse_text() -> str:
    return random.choice(EXCUSES)


def clarify_answer() -> str:
    return "что?"


def bot_q_answer() -> str:
    return "возможно"


# ===== GPT CHAT =====

def answer_info_fast(question: str) -> str:

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "не знаю"

    try:

        client = OpenAI(api_key=api_key)

        resp = client.responses.create(
            model="gpt-5",
            input=f"""
Ты — девушка по имени Веся.
Отвечай коротко, естественно, по-русски.
Не как ассистент. Как живой человек.

Сообщение:
{question}
""",
        )

        text = resp.output_text.strip()

        return text or "не знаю"

    except Exception:

        return "не сейчас"
