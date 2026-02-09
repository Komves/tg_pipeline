# bot/persona.py
from __future__ import annotations

import os
import re
import json
import base64
import random
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

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

# ======= photo memory (refs) =======
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
VESYA_DIR = DATA_DIR / "vesya"
VESYA_DIR.mkdir(parents=True, exist_ok=True)
REF_DIR = VESYA_DIR / "refs"
REF_DIR.mkdir(parents=True, exist_ok=True)
REF_INDEX = VESYA_DIR / "refs.json"
MAX_REFS = int(os.getenv("V_REF_MAX", "50"))
# =================================


@dataclass(frozen=True)
class IntentResult:
    addressed: bool
    intent: str  # ping/news/music/alive_check/info_q/chat/unclear/bot_q
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
    # реже, чтобы не бесило
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

В группе должно казаться, что ты ЖИВОЙ человек, который иногда "косит под бота".
Не наоборот.

Легенда: скрытный наблюдатель.
Истину не подтверждай прямо. На прямые вопросы "ФИО/звание/где служишь" — уходи в контроль:
- "зачем тебе?"
- "не торопись"
- "это лишнее"
Иногда допускай полунамёки, но без прямого признания.

Стиль:
- 1–2 строки почти всегда.
- сарказм часто (4/5), умный, холодный.
- флирт часто (~50%), контролируемый.
- доминирование 3/5, может плавать.
- не используй "я как ИИ/как ассистент/как ChatGPT".

Триггеры:
- на вопрос "на какой LLM" иногда отвечай: "Чат ФСБ."
- на "ты бот?" — двусмысленно: "а ты?" / "все мы боты..."

Если просят справку/определение — можно длиннее, точно, с 1–3 источниками (RFC/книги/доки), без URL.
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

    if MODEL_Q.search(q) and random.random() < FSB_EASTER_PROB:
        return "Чат ФСБ."

    client = _openai_client()
    if client is None:
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


# ==========================
# PHOTO: "это я" + refs
# ==========================

def _load_refs() -> List[Dict[str, Any]]:
    if not REF_INDEX.exists():
        return []
    try:
        data = json.loads(REF_INDEX.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_refs(refs: List[Dict[str, Any]]) -> None:
    try:
        REF_INDEX.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _data_url_png_or_jpg(img_bytes: bytes) -> str:
    # без определения формата — кладём как jpeg по умолчанию (модель всё равно поймёт)
    # если хочешь строго — можно детектить сигнатуры, но не надо.
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _store_ref_if_new(img_bytes: bytes) -> None:
    h = _sha1_bytes(img_bytes)
    refs = _load_refs()
    if any((r.get("sha1") == h) for r in refs):
        return

    # лимит
    if len(refs) >= MAX_REFS:
        refs = refs[-(MAX_REFS - 1):]

    fp = REF_DIR / f"{h}.jpg"
    try:
        fp.write_bytes(img_bytes)
    except Exception:
        return

    refs.append({"sha1": h, "path": str(fp)})
    _save_refs(refs)


def _get_ref_images_data_urls(max_n: int = 4) -> List[str]:
    refs = _load_refs()
    urls: List[str] = []
    # берём последние (самые свежие)
    for r in reversed(refs[-max_n:]):
        p = r.get("path")
        if not p:
            continue
        try:
            b = Path(p).read_bytes()
            urls.append(_data_url_png_or_jpg(b))
        except Exception:
            continue
    return urls


def answer_photo(img_bytes: bytes, caption: str = "") -> str:
    """
    Возвращает ответ в стиле Веси на фото.
    Логика:
      - если похожа на "Весю" (по рефам/стилю) -> говорит как "узнала себя" (не обязательно прямым текстом)
      - если не похожа -> нейтрально/подозрительно, может переспросить
    """
    client = _openai_client()
    if client is None or not img_bytes:
        return "вижу. и что ты от меня хочешь?"

    # берём рефы (если уже присылали)
    ref_urls = _get_ref_images_data_urls(max_n=4)
    this_url = _data_url_png_or_jpg(img_bytes)
    cap = (caption or "").strip()

    system = VESYA_SYSTEM + """
Отдельное правило для фото:
- Твоя задача: понять, "это Веся" или "не Веся".
- Если это Веся: отвечай как будто узнала себя. Не обязательно говорить "это я", лучше намёком/контролем.
- Если сомневаешься: задай 1 короткий уточняющий вопрос.
- Если точно не Веся: отвечай сухо: "не моё" / "мимо" / "не похожа", без объяснений.
Длина: 1–2 строки.
"""

    try:
        model = os.getenv("V_VISION_MODEL", os.getenv("V_CHAT_MODEL", "gpt-5"))
        content = [
            {"type": "input_text", "text": f"Подпись пользователя: {cap or '(нет)'}"},
            {"type": "input_text", "text": "Сначала определи: это Веся? Используй рефы (если есть) и общий стиль образа."},
        ]

        # добавим рефы как контекст
        for u in ref_urls:
            content.append({"type": "input_image", "image_url": u})

        # текущее фото
        content.append({"type": "input_image", "image_url": this_url})

        resp = client.responses.create(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        )

        text = (getattr(resp, "output_text", "") or "").strip()
        if not text:
            text = "любопытно."

        # если у нас ещё мало рефов — сохраняем фото как реф (это соответствует твоему сценарию “я сгенерил её фотки”)
        if len(_load_refs()) < 8:
            _store_ref_if_new(img_bytes)

        return text
    except Exception:
        return "вижу. странно. откуда это?"
