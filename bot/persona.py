# bot/persona.py
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


# --- name detection (Веся / Весь / Веська / т.п.) ---
NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я))([\s,!.?:;]|$)", re.IGNORECASE)

# intents
NEWS_RE = re.compile(r"\b(новост|сводк|че там в мире|что в мире|что происходит|в мире)\b", re.IGNORECASE)
MUSIC_RE = re.compile(r"\b(музык|музон|кавер|cover|что послушать|ютуб|youtube)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(кто жив|есть кто жив|кто тут|все спят|живые есть|кто не спит)\b", re.IGNORECASE)

INFO_Q_RE = re.compile(r"\b(что такое|как работает|как сделать|почему|зачем|объясни)\b", re.IGNORECASE)


ACKS = [
    "сек",
    "ща",
    "смотрю",
    "ищу",
    "подожди",
]

CLARIFY = [
    "что именно",
    "уточни",
    "конкретнее",
    "про что ты",
    "сформулируй нормально",
]

ALIVE_ANSWERS = [
    "я здесь",
    "вижу",
    "пока тихо",
    "живые есть, но прячутся",
    "похоже, ты один",
]

BOT_Q = re.compile(r"\b(ты бот|бот ли ты|ты человек)\b", re.IGNORECASE)
BOT_Q_ANSWERS = [
    "а ты?",
    "мы все боты в этом мире",
    "зависит от определения",
    "это принципиально?",
]

EXCUSES = [
    "отвлеклась",
    "была на связи с начальством",
    "вышла за вином",
    "носик пудрила",
    "не могла ответить",
]


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str  # get12/news/music/alive_check/info_q/unclear
    question: str = ""  # for info_q


def is_addressed(text: str) -> bool:
    return bool(NAME_RE.search(text or ""))


def strip_name_prefix(text: str) -> str:
    t = (text or "").strip()
    # убираем первое вхождение имени + знаки после него
    t = NAME_RE.sub(" ", t, count=1).strip()
    return t


def detect_intent(text: str) -> IntentResult:
    raw = (text or "").strip()
    addressed = is_addressed(raw)
    t = strip_name_prefix(raw) if addressed else raw

    # "ты бот?"
    if addressed and BOT_Q.search(t):
        return IntentResult(addressed=True, intent="bot_q", question="")

    if addressed and ALIVE_RE.search(t):
        return IntentResult(addressed=True, intent="alive_check", question="")

    if addressed and NEWS_RE.search(t):
        return IntentResult(addressed=True, intent="news", question="")

    if addressed and MUSIC_RE.search(t):
        return IntentResult(addressed=True, intent="music", question="")

    # info question
    if addressed and (("?" in t) or INFO_Q_RE.search(t)):
        q = t.strip()
        if len(q) >= 6:
            return IntentResult(addressed=True, intent="info_q", question=q)

    if addressed:
        # если очень коротко/невнятно — уточняем
        short = re.sub(r"\s+", " ", t).strip()
        if len(short) <= 2 or short in {"ну", "давай", "нормально", "жги", "огня", "че", "что"}:
            return IntentResult(addressed=True, intent="unclear", question="")
        # по умолчанию — основной пайплайн
        return IntentResult(addressed=True, intent="get12", question="")

    return IntentResult(addressed=False, intent="none", question="")


def maybe_ack() -> Optional[str]:
    # иногда отправляем короткое "смотрю/сек"
    if random.random() < 0.55:
        return random.choice(ACKS)
    return None


def maybe_delay_seconds_for_ping() -> Optional[int]:
    # иногда "пропадает" на 2–5 минут
    if random.random() < 0.28:
        return random.randint(120, 300)
    return None


def answer_bot_q() -> str:
    return random.choice(BOT_Q_ANSWERS)


def answer_alive() -> str:
    return random.choice(ALIVE_ANSWERS)


def answer_clarify() -> str:
    return random.choice(CLARIFY)


def excuse_text() -> str:
    return random.choice(EXCUSES)


def answer_info_fast(question: str) -> str:
    """
    Быстрый ответ: GPT-5 без web_search. Если ключа нет — короткий fallback.
    """
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        # fallback без палевных "нет доступа"
        return "сформулируй чуть конкретнее. я отвечу."

    try:
        client = OpenAI()
        resp = client.responses.create(
            model=os.getenv("V_INFO_MODEL", "gpt-5"),
            input=(
                "Отвечай по-русски. Коротко и по делу. "
                "Стиль: разговорный, иногда саркастичный, но без клоунады. "
                "Если вопрос неоднозначный — задай 1 уточняющий вопрос.\n\n"
                f"Вопрос: {question}"
            ),
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        return text or "не люблю пустые вопросы. уточни."
    except Exception:
        return "не сейчас. уточни вопрос — попробую нормально."
