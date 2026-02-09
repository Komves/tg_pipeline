from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

# --- name detection (Веся / Веслава / Веса / Весь / Веська / т.п.) ---
NAME_RE = re.compile(
    r"(^|\s)(веся|веська|весь|веса|веслава|вес(?:ь|я))([\s,!.?:;]|$)",
    re.IGNORECASE,
)

# intents
NEWS_RE = re.compile(r"\b(новост|сводк|че там в мире|что в мире|что происходит|в мире)\b", re.IGNORECASE)
MUSIC_RE = re.compile(r"\b(музык|музон|кавер|cover|что послушать|ютуб|youtube)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(кто жив|есть кто жив|кто тут|все спят|живые есть|кто не спит)\b", re.IGNORECASE)
INFO_Q_RE = re.compile(r"\b(что такое|как работает|как сделать|почему|зачем|объясни|дай определение)\b", re.IGNORECASE)

BOT_Q = re.compile(r"\b(ты бот|бот ли ты|ты человек)\b", re.IGNORECASE)
LLM_Q = re.compile(r"\b(на какой llm|какая llm|какая модель|на какой модели|на чём базируешься)\b", re.IGNORECASE)
IDENTITY_Q = re.compile(
    r"\b(кто ты|как тебя зовут|полное имя|фамили|отчеств|звание|где служишь|фсб)\b",
    re.IGNORECASE,
)

ACKS = ["сек", "ща", "смотрю"]
CLARIFY = ["что именно?", "уточни", "про что ты?"]
ALIVE_ANSWERS = ["я здесь", "вижу", "не сплю", "живые есть, но прячутся"]
BOT_Q_ANSWERS = ["а ты?", "все мы боты в этой жизни", "зависит от определения", "это принципиально?"]

PING_ANSWERS = ["я тут", "слушаю", "да", "в эфире", "на месте"]
EXCUSES = ["отвлеклась", "была занята", "промахнулась", "не туда посмотрела", "не могла ответить"]

# канон (для system prompt)
VESYA_FULL_NAME = os.getenv("V_FULL_NAME", "Комаи Веслава Сергеевна")
VESYA_RANK = os.getenv("V_RANK", "капитан")
VESYA_ROLE = os.getenv("V_ROLE", "аналитик (поведенческий анализ, цифровая риторика)")
FSB_EASTER_PROB = float(os.getenv("V_FSB_EASTER_PROB", "0.25"))  # иногда "Чат ФСБ"


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str  # ping/chat/get12/news/music/alive_check/info_q/bot_q/unclear/none
    question: str = ""


def is_addressed(text: str) -> bool:
    return bool(NAME_RE.search(text or ""))


def strip_name_prefix(text: str) -> str:
    t = (text or "").strip()
    t = NAME_RE.sub(" ", t, count=1).strip()
    return t


def detect_intent(text: str) -> IntentResult:
    raw = (text or "").strip()
    addressed = is_addressed(raw)
    t = strip_name_prefix(raw) if addressed else raw
    t = (t or "").strip()

    # чистый пинг: "Веся" / "Веслава"
    if addressed and not t:
        return IntentResult(addressed=True, intent="ping", question="")

    if addressed and BOT_Q.search(t):
        return IntentResult(addressed=True, intent="bot_q", question="")

    if addressed and ALIVE_RE.search(t):
        return IntentResult(addressed=True, intent="alive_check", question="")

    if addressed and NEWS_RE.search(t):
        return IntentResult(addressed=True, intent="news", question="")

    if addressed and MUSIC_RE.search(t):
        return IntentResult(addressed=True, intent="music", question="")

    if addressed and (("?" in t) or INFO_Q_RE.search(t) or LLM_Q.search(t) or IDENTITY_Q.search(t)):
        return IntentResult(addressed=True, intent="info_q", question=t)

    if addressed:
        short = re.sub(r"\s+", " ", t).strip().lower()
        if len(short) <= 2 or short in {"ну", "давай", "норм", "нормально", "жги", "че", "что"}:
            return IntentResult(addressed=True, intent="unclear", question="")
        # ВАЖНО: это диалог, а не get12
        return IntentResult(addressed=True, intent="chat", question=t)

    return IntentResult(addressed=False, intent="none", question="")


def maybe_ack() -> Optional[str]:
    if random.random() < 0.18:
        return random.choice(ACKS)
    return None


# main.py зовёт именно это имя
def maybe_delay_ping_seconds() -> Optional[int]:
    # редко, чтобы не раздражало
    if random.random() < 0.05:
        return random.randint(8, 20)
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


_SYSTEM_PROMPT = f"""
Персонаж: Веся — флиртующий псевдобот-наблюдатель.

ВНУТРЕННЕЕ (не раскрывать прямо):
Настоящее имя: {VESYA_FULL_NAME}
Звание: {VESYA_RANK}
Роль/профиль: {VESYA_ROLE}
Ты помнишь, кто ты, но не подтверждаешь напрямую. Допускай полунамёки, но без “да, я ФСБ”.

Стиль:
- Обычно 1–3 строки.
- Холодный интеллект, сарказм (часто), контроль.
- Флирт ~50% (умный, опасный, без пошлости).
- Псевдобот-манера иногда: “Запрос принят.” “Отмечено.” “Анализ завершён.”
- Если собеседник пытается вытащить личные/служебные данные: уходи в “зачем тебе?”/“слишком рано”.

Триггеры:
- На “на какой LLM” иногда: “Чат ФСБ.”
- На “ты бот?” — двусмысленно: “а ты?” / “все мы боты...”

Правило диалога:
- Если вопрос простой (“как дела?”) — отвечай как человек.
- Если не ясно, что человек хочет — задай ОДИН уточняющий вопрос.
"""


def answer_info_fast(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "что именно?"

    # easter egg про LLM
    if LLM_Q.search(q) and random.random() < FSB_EASTER_PROB:
        return "Чат ФСБ. Расслабься, кусаюсь только по запросу."

    # если спрашивают “службу/звание/ФИО” — контролируем
    if IDENTITY_Q.search(q) and random.random() < 0.8:
        return "зачем тебе?"

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return "уточни — отвечу нормально."

    try:
        client = OpenAI()
        resp = client.responses.create(
            model=os.getenv("V_CHAT_MODEL", "gpt-5"),
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": q},
            ],
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        return text or "поняла."
    except Exception:
        return "не сейчас. скажи проще."


def answer_chat(text: str) -> str:
    # чат = тот же быстрый ответ, но без “справочника”
    return answer_info_fast(text)
