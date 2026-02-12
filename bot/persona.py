from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

# =========================
# NAME / ADDRESSING
# =========================
NAME_RE = re.compile(
    r"(^|\s)(веся|веська|весь|веса|веслава|вес(?:ь|я)|vesya)([\s,!.?:;]|$)",
    re.IGNORECASE,
)

def is_addressed(text: str) -> bool:
    return bool(NAME_RE.search(text or ""))

def strip_name_prefix(text: str) -> str:
    t = (text or "").strip()
    t = NAME_RE.sub(" ", t, count=1).strip()
    return t

# =========================
# INTENT REGEX
# =========================
NEWS_RE = re.compile(r"\b(новост|сводк|дайджест|что в мире|че там|что происходит|news)\b", re.IGNORECASE)
ALIVE_RE = re.compile(r"\b(кто жив|живые есть|кто тут|не спит|ты тут)\b", re.IGNORECASE)
INFO_Q_RE = re.compile(r"\b(что такое|как работает|как сделать|почему|зачем|объясни|дай определение)\b", re.IGNORECASE)

BOT_Q = re.compile(r"\b(ты бот|бот ли ты|ты человек)\b", re.IGNORECASE)
LLM_Q = re.compile(r"\b(какая модель|на какой модели|на чем работаешь|какой llm|какая llm)\b", re.IGNORECASE)
IDENTITY_Q = re.compile(r"\b(кто ты|как тебя зовут|полное имя|фамили|отчеств|звание|где служишь)\b", re.IGNORECASE)

# explicit content / run
CONTENT_EXPLICIT_RE = re.compile(
    r"\b(контент|пост|посты|идеи|мем|мемы|видос|видосы|видео|накидай|погнали|get12|прогон|ингест|ingest)\b",
    re.IGNORECASE,
)

# ignite / “жги”
IGNITE_RE = re.compile(
    r"\b(жги|зажги|огня|дай огня|дай жару|врубай|погнали|поехали)\b",
    re.IGNORECASE,
)

# choice responses
CHOICE_CONTENT_RE = re.compile(r"\b(контент|пост|мем|видос|видео|get12|прогон|ингест|ingest)\b", re.IGNORECASE)
CHOICE_NEWS_RE = re.compile(r"\b(новост|дайджест|в мире|news)\b", re.IGNORECASE)
CHOICE_STRIP_RE = re.compile(r"\b(стриптиз|разденься|нюд|nude|эротик)\b", re.IGNORECASE)

# =========================
# STYLE CONTROL (MODE 3)
# =========================
# Мат “регулярный”: вероятность вставки жёсткого слова в ответ.
# Держим вменяемо, чтобы не материлась в каждом слове.
PROFANITY_P = float(os.getenv("V_PROFANITY_P", "0.65"))

# Мат запрещаем, если пользователь явно просит “без мата”
NO_SWEAR_RE = re.compile(r"\b(без мата|не матерись|без ругани)\b", re.IGNORECASE)

# “умный” мат/инвективы: без наездов на защищённые группы
SWEAR_SOFT = [
    "блядь",
    "нахуй",
    "хуйня",
    "пиздец",
    "сука",
]
SWEAR_EDGE = [
    "охуенно",
    "заебись",
    "ебать",
]

def _maybe_swear(text: str, allow: bool = True) -> str:
    if not allow:
        return text
    if random.random() > PROFANITY_P:
        return text
    # вставляем аккуратно: либо в начале коротким маркером, либо усиливаем одно слово
    w = random.choice(SWEAR_SOFT if random.random() < 0.8 else SWEAR_EDGE)
    variants = [
        f"{w}. {text}",
        f"{text} {w}.",
    ]
    return random.choice(variants)

def _answer_first_rule(system_prompt: str) -> str:
    return (
        system_prompt
        + "\n\nКРИТИЧЕСКОЕ ПРАВИЛО: сначала дай утверждение/ответ/позицию. "
          "Не начинай с уточняющего вопроса. В конце можно максимум ОДИН короткий вопрос, если прям нужно."
    )

# =========================
# PUBLIC PACKS used by chatgpt_dialog.py
# =========================
PING_ANSWERS = [
    "я тут.",
    "в эфире.",
    "слушаю.",
    "на месте.",
    "да.",
]

ALIVE_ANSWERS = [
    "я здесь.",
    "не сплю.",
    "вижу чат.",
    "живые есть. я — точно.",
]

BOT_Q_ANSWERS = [
    "а ты как думаешь?",
    "это тебя правда волнует?",
    "люди тоже иногда звучат как скрипты. продолжай.",
    "вопрос не про меня. вопрос — зачем тебе ответ.",
]

CHOICE_PROMPTS = [
    "Выбирай: контент / новости. Третье — мимо.",
    "Контент или новости? Быстро.",
    "Ну? Контент или новости. Я не люблю ждать.",
]

STRIP_REPLIES = [
    "уровень доступа недостаточен.",
    "запрос отклонён. но попытка дерзкая.",
    "не сегодня. и не здесь.",
    "оформи допуск — тогда поговорим.",
]

# =========================
# CANON (used inside system prompt)
# =========================
VESYA_CALLSIGN = os.getenv("V_CALLSIGN", "Веся")
FSB_EASTER_PROB = float(os.getenv("V_FSB_EASTER_PROB", "0.30"))

_SYSTEM_PROMPT = f"""
Ты — {VESYA_CALLSIGN}. Ты человек, который намеренно притворяется ботом.
Тон: холодный, уверенный, доминирующий. Сарказм обязателен.
Флирт умный, опасный, без пошлости.
ВАЖНО: не говори «я просто бот/ассистент» как дефолт.

МАТ РАЗРЕШЕН (режим 3):
- Мат регулярный, но дозированный: 0–2 матерных слова в ответ максимум.
- Мат — инструмент давления/доминирования/сарказма, а не истерики.
- Не используй мат в каждом предложении.
- Не оскорбляй по признакам защищённых групп.

Диалог:
- Сначала ответ/позиция. Потом максимум один вопрос (или без вопросов).
"""

# =========================
# INTENT RESULT
# =========================
@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str  # ping/chat/run_all/news/alive_check/info_q/bot_q/ignite_choice/unclear/none
    question: str = ""

# =========================
# INTENT DETECTION
# =========================
def detect_intent(text: str) -> IntentResult:
    raw = (text or "").strip()
    addressed = is_addressed(raw)
    t = strip_name_prefix(raw) if addressed else raw
    t = (t or "").strip()

    if addressed and not t:
        return IntentResult(addressed=True, intent="ping", question="")

    if addressed and BOT_Q.search(t):
        return IntentResult(addressed=True, intent="bot_q", question=t)

    if addressed and ALIVE_RE.search(t):
        return IntentResult(addressed=True, intent="alive_check", question=t)

    if addressed and NEWS_RE.search(t):
        return IntentResult(addressed=True, intent="news", question=t)

    if addressed and IGNITE_RE.search(t):
        # dialog layer может сразу маршрутизировать в content; но оставим intent для совместимости
        return IntentResult(addressed=True, intent="ignite_choice", question=t)

    if addressed and CONTENT_EXPLICIT_RE.search(t):
        return IntentResult(addressed=True, intent="run_all", question=t)

    if addressed and INFO_Q_RE.search(t):
        return IntentResult(addressed=True, intent="info_q", question=t)

    if addressed and t:
        return IntentResult(addressed=True, intent="chat", question=t)

    return IntentResult(addressed=addressed, intent="none", question=t)

# =========================
# LLM CALL
# =========================
def _call_llm(system_prompt: str, user_text: str, model_env: str) -> str:
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return ""
    client = OpenAI()
    resp = client.responses.create(
        model=os.getenv(model_env, "gpt-4o-mini"),
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    )
    return (getattr(resp, "output_text", "") or "").strip()

# =========================
# ANSWERS
# =========================
def answer_ping() -> str:
    return random.choice(PING_ANSWERS)

def answer_alive() -> str:
    return random.choice(ALIVE_ANSWERS)

def answer_bot_q() -> str:
    return random.choice(BOT_Q_ANSWERS)

def strip_reply() -> str:
    return random.choice(STRIP_REPLIES)

def answer_info_fast(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "говори."

    if NO_SWEAR_RE.search(q):
        allow_swear = False
    else:
        allow_swear = True

    if LLM_Q.search(q) and random.random() < FSB_EASTER_PROB:
        txt = "Чат ФСБ. Расслабься — если бы хотела, ты бы уже молчал."
        return _maybe_swear(txt, allow=allow_swear)

    if IDENTITY_Q.search(q):
        txt = "слишком рано. не туда копаешь."
        # 1 вопрос максимум — и только после позиции
        txt = txt + " зачем тебе это?"
        return _maybe_swear(txt, allow=allow_swear)

    sys = _answer_first_rule(_SYSTEM_PROMPT)
    out = _call_llm(sys, q, "V_CHAT_MODEL")
    out = out or "поняла. дальше?"
    return _maybe_swear(out, allow=allow_swear)

def answer_chat(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "говори."

    allow_swear = not NO_SWEAR_RE.search(t)

    # быстрые правила, чтобы не было “вопрос на вопрос”
    if re.search(r"\b(как сама|как ты|как дела)\b", t, re.IGNORECASE):
        base = "нормально. собрана. голова холодная."
        # один вопрос максимум
        base += " ты по делу или просто скучаешь?"
        return _maybe_swear(base, allow=allow_swear)

    if re.search(r"\b(о тебе|про тебя)\b", t, re.IGNORECASE):
        base = "про меня — дозировано. ты сначала скажи, зачем тебе это."
        return _maybe_swear(base, allow=allow_swear)

    if BOT_Q.search(t):
        base = random.choice(BOT_Q_ANSWERS)
        return _maybe_swear(base, allow=allow_swear)

    # LLM общий
    sys = _answer_first_rule(_SYSTEM_PROMPT)
    out = _call_llm(sys, t, "V_CHAT_MODEL")
    out = out or "мимо. сформулируй нормально."
    return _maybe_swear(out, allow=allow_swear)
