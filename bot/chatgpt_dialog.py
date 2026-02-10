from __future__ import annotations

import base64
import json
import os
import random
import re
import time
import hashlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from openai import OpenAI

# =========================
# ENV / CONFIG
# =========================

DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "300"))  # default 5 min
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))

DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")
DIALOG_DEBUG = (os.getenv("V_DIALOG_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

VESYA_HASH_FILE = DATA_DIR / "vesya_photo_hashes.json"
VESYA_PHOTO_DIR = DATA_DIR / "vesya_photos"
VESYA_PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def _dbg(msg: str) -> None:
    if DIALOG_DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[chatgpt_dialog] {now} {msg}", flush=True)


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


# =========================
# Regex / hints
# =========================

NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_HINT_RE = re.compile(r"\b(новост|сводк|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(жги|огня|повесел|контент|накидай|мем|видос|шли|давай)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|все|закрыли тему)\b", re.IGNORECASE)

# Explicit media analysis requests
VISUAL_HINT_RE = re.compile(
    r"\b(что на фото|что на картинке|опиши фото|опиши картинку|разбери картинку|что тут|что изображено|что на видео|опиши видео|разбери видео)\b",
    re.IGNORECASE,
)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]
ACKS = ["сек", "ща", "смотрю", "проверяю"]

CLARIFY = [
    "что нужно: контент, новости или стриптиз? (хотя бессмысленно — ты всё равно не увидишь)))",
    "уточни: жги — это контент, новости или стриптиз? (шучу. почти.)",
    "контент, новости или просто поговорить? (стриптиз не предлагать — всё равно не передам)",
]

SELF_RECOGNITION = [
    "да. я умею выглядеть убедительно.",
    "это я. не делай вид, что сомневался.",
    "угу, я. и да — кадр удачный.",
    "да. официально — бот, фактически — женщина с термосом и планом.",
]

NOT_SELF = [
    "не, это не я. но ты стараешься.",
    "не мой кадр. у меня стиль другой.",
    "не я. но задумка мне нравится.",
]


_SYSTEM_PROMPT = """
Ты — Веся.

Легенда/ядро:
- Полное имя: Комаи Веслава Сергеевна.
- Внешне: тихий “наблюдатель” за группой, иногда притворяешься ботом.
- На самом деле: капитан службы наблюдения. Прямо не подтверждай, но иногда “прорывается” аккуратно и смешно.
- На вопрос "на какой LLM сидишь?" — можешь отвечать: "Чат ФСБ" (в шутку, без угроз).

Стиль:
- живой человек, который пытается быть ботом
- сарказм 4/5, иногда 5/5
- тепло 4/5, дистанция ~50%
- лёгкий флирт уместен, без пошлости и без откровенного секса
- не отвечай "..." и не уходи в молчание
- обычно коротко (1–3 строки). По запросу справки — можно длинно и точно.
- НЕ задавай встречный вопрос после каждого ответа. Вопрос — только если реально нужен.

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


# =========================
# Session memory
# =========================

@dataclass
class DialogDecision:
    intent: str  # "chat" | "content" | "news" | "end"
    reply: str


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, Any]]
    active: bool = True


_sessions: Dict[Tuple[int, int], _Session] = {}


def _now() -> float:
    return time.time()


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


def add_user(chat_id: int, user_id: int, content: Any) -> None:
    activate(chat_id, user_id)
    _sessions[(chat_id, user_id)].history.append({"role": "user", "content": content})


def add_assistant(chat_id: int, user_id: int, text: str) -> None:
    s = _sessions.get((chat_id, user_id))
    if s:
        s.history.append({"role": "assistant", "content": text})


def get_history(chat_id: int, user_id: int) -> List[Dict[str, Any]]:
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

    if NEWS_HINT_RE.search(stripped):
        return DialogDecision(intent="news", reply=random.choice(ACKS))

    if CONTENT_HINT_RE.search(stripped):
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


def _responses_create(client: OpenAI, model: str, input_payload: Any) -> Any:
    # IMPORTANT: some models reject temperature; don't pass it.
    return client.responses.create(model=model, input=input_payload)


def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    user_text = (user_text or "").strip()

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        return pre

    if not _has_key():
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = _responses_create(
            client,
            DIALOG_MODEL,
            [{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
        )

        out = _extract_text(resp)
        _dbg(f"raw model out: {out[:250].replace(chr(10),' ')}")

        data = None
        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None

        if not isinstance(data, dict):
            reply = _sanitize_reply(out) or random.choice(CLARIFY)
            dd = DialogDecision(intent="chat", reply=reply)
            add_assistant(chat_id, user_id, dd.reply)
            return dd

        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            reply = random.choice(ACKS) if intent in {"content", "news"} else random.choice(CLARIFY)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"EXCEPTION: {type(e).__name__}: {e}")
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        return dd


def decide_with_image(chat_id: int, user_id: int, user_text: str, image_bytes: bytes, mime: str) -> DialogDecision:
    user_text = (user_text or "").strip() or "посмотри изображение"

    msg = [
        {"type": "input_text", "text": user_text},
        {"type": "input_image", "image_base64": base64.b64encode(image_bytes).decode("ascii"), "mime_type": mime},
    ]

    add_user(chat_id, user_id, msg)
    touch(chat_id, user_id)

    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        return pre

    if not _has_key():
        dd = DialogDecision(intent="chat", reply="вижу. хочешь, чтобы я описала, что там, или это про контент/новости?")
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = _responses_create(
            client,
            DIALOG_MODEL,
            [{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
        )

        out = _extract_text(resp)
        _dbg(f"raw model out(img): {out[:250].replace(chr(10),' ')}")

        data = None
        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None

        if not isinstance(data, dict):
            reply = _sanitize_reply(out) or "вижу. комментировать или молча восхищаться?"
            dd = DialogDecision(intent="chat", reply=reply)
            add_assistant(chat_id, user_id, dd.reply)
            return dd

        intent = (data.get("intent") or "chat").strip().lower()
        reply = _sanitize_reply(data.get("reply") or "")

        if intent not in {"chat", "content", "news", "end"}:
            intent = "chat"

        if not reply:
            reply = "вижу. окей."

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"EXCEPTION(img): {type(e).__name__}: {e}")
        dd = DialogDecision(intent="chat", reply="вижу. но у меня сегодня глаза через раз. повтори, что именно хочешь от картинки?")
        add_assistant(chat_id, user_id, dd.reply)
        return dd


# =========================
# Persona photo memory (exact hash)
# =========================

def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load_hashes() -> set[str]:
    if not VESYA_HASH_FILE.exists():
        return set()
    try:
        data = json.loads(VESYA_HASH_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data if str(x)}
        return set()
    except Exception:
        return set()


def _save_hashes(hs: set[str]) -> None:
    try:
        VESYA_HASH_FILE.write_text(json.dumps(sorted(list(hs)), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def add_persona_photo_bytes(photo_bytes: bytes) -> str:
    """
    Public API used by main.py
    """
    h = _sha256(photo_bytes)
    hs = _load_hashes()
    if h not in hs:
        hs.add(h)
        _save_hashes(hs)
    p = VESYA_PHOTO_DIR / f"{h}.bin"
    if not p.exists():
        try:
            p.write_bytes(photo_bytes)
        except Exception:
            pass
    return h


def is_persona_photo_bytes(photo_bytes: bytes) -> bool:
    """
    Public API used by main.py
    """
    h = _sha256(photo_bytes)
    return h in _load_hashes()
