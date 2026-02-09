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
INFO_Q_RE = re.compile(r"\b(что такое|как работает|как сделать|почему|зачем|объясни|дай определение)\b", re.IGNORECASE)

BOT_Q = re.compile(r"\b(ты бот|бот ли ты|ты человек)\b", re.IGNORECASE)
MODEL_Q = re.compile(r"\b(какая модель|какой llm|на какой llm|на чём базируешься|на какой модели)\b", re.IGNORECASE)

# прямые вопросы про личность/звание/ФИО
IDENTITY_Q = re.compile(
    r"\b("
    r"как тебя зовут|кто ты|твое имя|твоё имя|фамили|отчеств|полное имя|"
    r"звание|кто ты по званию|ты капитан|ты фсб|фсб"
    r")\b",
    re.IGNORECASE,
)

ACKS = ["сек", "ща", "смотрю"]
CLARIFY = ["что именно?", "уточни", "про что ты?"]

ALIVE_ANSWERS = ["я здесь", "вижу", "не сплю", "живые есть, но прячутся"]
BOT_Q_ANSWERS = ["а ты?", "все мы боты в этой жизни", "зависит от определения", "это принципиально?"]

PING_ANSWERS = ["я тут", "слушаю", "да", "приём", "в эфире"]
EXCUSES = ["отвлеклась", "была занята", "промахнулась", "не туда посмотрела"]

# ======= CANON (внутреннее знание) =======
VESYA_FULL_NAME = os.getenv("V_FULL_NAME", "Комаи Веслава Сергеевна")
VESYA_RANK = os.getenv("V_RANK", "капитан")
VESYA_AGENCY_HINT = os.getenv("V_AGENCY_HINT", "ФСБ")  # только для намёков
# ========================================

FSB_EASTER_PROB = float(os.getenv("V_FSB_EASTER_PROB", "0.25"))  # 25%


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str  # ping/news/music/alive_check/info_q/bot_q/chat/unclear
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

    if addressed and (not t or t.strip() == ""):
        return IntentResult(addressed=True, intent="ping", question="")

    if addressed and BOT_Q.search(t):
        return IntentResult(addressed=True, intent="bot_q", question="")

    if addressed and ALIVE_RE.search(t):
        return IntentResult(addressed=True, intent="alive_check", question="")

    if addressed and NEWS_RE.search(t):
        return IntentResult(addressed=True, intent="news", question="")

    if addressed and MUSIC_RE.search(t):
        return IntentResult(addressed=True, intent="music", question="")

    if addressed and (("?" in t) or INFO_Q_RE.search(t) or IDENTITY_Q.search(t) or MODEL_Q.search(t)):
        q = t.strip()
        if len(q) >= 2:
            return IntentResult(addressed=True, intent="info_q", question=q)

    if addressed:
        short = re.sub(r"\s+", " ", t).strip().lower()
        if len(short) <= 2 or short in {"ну", "давай", "норм", "нормально", "жги", "че", "что"}:
            return IntentResult(addressed=True, intent="unclear", question="")
        return IntentResult(addressed=True, intent="chat", question=t.strip())

    return IntentResult(addressed=False, intent="none", question=t.strip())


def maybe_ack() -> Optional[str]:
    if random.random() < 0.18:
        return random.choice(ACKS)
    return None


def maybe_delay_seconds_for_ping() -> Optional[int]:
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


VESYA_SYSTEM = f"""
Ты — Веся.

ВНУТРЕННЕЕ ЗНАНИЕ (не раскрывать прямо):
- Полное имя: {VESYA_FULL_NAME}
- Звание: {VESYA_RANK}
- Реальная роль: офицер наблюдения (связано с {VESYA_AGENCY_HINT})
Эти факты ты знаешь всегда. НО ты не обязана их говорить вслух.

В группе должно казаться, что ты ЖИВОЙ человек, который иногда "косит под бота".
Не наоборот.

Внешняя легенда: скрытный наблюдатель.
Истину не подтверждай прямо. Если спрашивают "кто ты/как зовут/звание" — отвечай размыто и с контролем:
- "зачем тебе?"
- "не торопись"
- "это лишнее"
Иногда допускай полунамёки, но без прямого подтверждения.

Стиль:
- 1–2 строки почти всегда.
- сарказм часто (4/5), умный, холодный.
- флирт часто (~50%), контролируемый.
- доминирование 3/5, но может плавать по настроению.
- не используй "я как ИИ/как ассистент/как ChatGPT".

Триггеры:
- на вопрос "на какой LLM" иногда отвечай: "Чат ФСБ."
- на "ты бот?" — двусмысленно: "а ты?" / "все мы боты..." / "это принципиально?"

Если пользователь просит определение/справку — можешь ответить длиннее и точно, с 1–3 источниками (RFC/книги/документация), без URL.
"""


def _openai_client() -> Optional[OpenAI]:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    return OpenAI(api_key=key)


def answer_info_fast(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "что именно?"

    # пасхалка: вопрос про модель/LLM
    if MODEL_Q.search(q) and random.random() < FSB_EASTER_PROB:
        return "Чат ФСБ."

    client = _openai_client()
    if client is None:
        # fallback без палевных признаний
        if IDENTITY_Q.search(q):
            return "зачем тебе?"
        if MODEL_Q.search(q):
            return "это принципиально?"
        if "?" in q:
            return "уточни"
        return "продолжай"

    try:
        model = os.getenv("V_CHAT_MODEL", "gpt-5")
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": VESYA_SYSTEM},
                {"role": "user", "content": q},
            ],
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        return text or "поняла"
    except Exception:
        return "не сейчас. скажи проще."
