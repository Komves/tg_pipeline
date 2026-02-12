from __future__ import annotations

import base64
import json
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from openai import OpenAI

# =========================
# CONFIG
# =========================
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", "300"))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", "18"))

DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")
VISION_MODEL = os.getenv("V_VISION_MODEL", DIALOG_MODEL)

DIALOG_DEBUG = (os.getenv("V_DIALOG_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}

LAST_PHOTO_TTL_SEC = int(os.getenv("V_LAST_PHOTO_TTL_SEC", "300"))  # 5 minutes default

PERSONA_DIR = DATA_DIR / "vesya_persona"
PERSONA_DIR.mkdir(parents=True, exist_ok=True)
PERSONA_INDEX = PERSONA_DIR / "index.json"

LAST_PHOTO_DIR = DATA_DIR / "vesya_last_photo"
LAST_PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def _dbg(msg: str) -> None:
    if DIALOG_DEBUG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[chatgpt_dialog] {now} {msg}", flush=True)


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


# =========================
# TYPES / SESSIONS
# =========================
@dataclass
class DialogDecision:
    intent: str  # "chat" | "content" | "news" | "end"
    reply: str


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True
    last_clarify_idx: Optional[int] = None


_sessions: Dict[Tuple[int, int], _Session] = {}


# =========================
# REGEX
# =========================
NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_HINT_RE = re.compile(r"\b(новост|сводк|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(жги|огня|дай огня|повесел|контент|давай|накидай|мем|видос|шли|прогон|ингест|ingest)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|все|закрыли тему)\b", re.IGNORECASE)

REMEMBER_HINT_RE = re.compile(r"\b(запомни|сохрани)\b", re.IGNORECASE)
IS_IT_YOU_RE = re.compile(r"\b(это ты\??|ты\??)\b", re.IGNORECASE)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]

_ACTION_ACKS_NEWS = [
    "сек, соберу новости",
    "сейчас посмотрю новости",
    "минутку — подбираю главное",
    "ок, уже ищу свежее",
    "поняла, собираю дайджест",
    "сейчас проверю, что нового",
]

_ACTION_ACKS_CONTENT = [
    "угу. сейчас принесу, что нашла",
    "ок, пошла собирать вкусное",
    "минутку — подбираю идеи",
    "сейчас накидаю контента",
    "поняла, уже собираю материалы",
]

# Clarify variants (deterministic pick + no repeat twice in a row)
CLARIFY = [
    "контент, новости или просто поговорить? стриптиз не обещаю, но потроллить — да 😌",
    "окей, уточни: новости, контент или поболтаем? (вариант «стриптиз» традиционно без гарантий 😏)",
    "я за любой кипиш. только скажи — новости, контент или чат? 😌",
    "что делаем: новости, контент или разговоры по душам? (стриптиз — в режиме «может быть» 😈)",
    "направление задай: новости / контент / поговорить. (остальное — по настроению 😌)",
    "выбирай меню: новости, контент или болталка. десерт не обещаю 😏",
    "так… жечь можно по-разному. новости? контент? или просто пообщаемся? 😌",
    "угу. а конкретнее — новости, контент или чат? (стриптиз — только словесный 😏)",
    "подтверди режим: новости / контент / поболтать. я уже морально готова 😌",
]

# Жёсткие отмазки, когда фото похоже на Весю
DENY_ME_HARD = [
    "Инфа по этому фото закрыта. Спроси что-нибудь попроще.",
    "Откуда у тебя это? Не, правда — откуда.",
    "Скажем так: лучше тебе это не хранить. Удали.",
    "Ты сейчас лезешь не туда. И да, я это заметила.",
    "Файл вижу. Происхождение — не обсуждаю. Дальше — без меня.",
    "Ты уверен, что хочешь продолжать? Мне — всё равно. Тебе — может быть нет.",
    "Плохая привычка — собирать чужие фото. Особенно такие.",
    "Это старое. И ты его не должен был видеть.",
    "Я не подтверждаю и не опровергаю. Но ты удали.",
]

DENY_NOT_ME = [
    "Не-а. Мимо.",
    "Похожа, но нет.",
    "Хорошая попытка, но это не я.",
    "Нет. И не надо на меня это вешать 😌",
]


# =========================
# SESSION API
# =========================
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


# =========================
# CLARIFY PICKER (deterministic + no immediate repeat)
# =========================
def _deterministic_index(n: int, seed_str: str) -> int:
    if n <= 0:
        return 0
    import hashlib

    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % n


def _pick_clarify(chat_id: int, user_id: int, user_text: str = "") -> str:
    s = _sessions.get((chat_id, user_id))
    if not CLARIFY:
        return ""
    seed = f"clarify:{chat_id}:{user_id}:{(user_text or '').strip().lower()}"
    idx = _deterministic_index(len(CLARIFY), seed)

    if not s:
        return CLARIFY[idx]

    last = getattr(s, "last_clarify_idx", None)
    if last is not None and idx == last and len(CLARIFY) > 1:
        idx = (idx + 1) % len(CLARIFY)

    s.last_clarify_idx = idx
    return CLARIFY[idx]


def _deterministic_pick(options: List[str], seed_str: str) -> str:
    if not options:
        return ""
    return options[_deterministic_index(len(options), seed_str)]


# =========================
# SEMANTIC GUARDS
# =========================
def _looks_like_clarification(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    rl = r.lower()
    if "?" in r:
        return True
    tokens = [
        "какие",
        "какая",
        "какое",
        "какие именно",
        "что именно",
        "уточни",
        "уточните",
        "про что",
        "что тебя интересует",
        "что вас интересует",
        "какая категория",
        "какой именно",
        "укажи",
        "выбери",
        "скажи какие",
    ]
    return any(t in rl for t in tokens)


def _looks_like_meta_pipeline(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    rl = r.lower()
    bad = [
        "пайплайн",
        "pipeline",
        "main.py",
        "в main.py",
        "не подключен",
        "не подключён",
        "не реализован",
        "не реализовано",
        "в этом проекте",
        "в коде",
        "в репозитории",
        "не могу",
        "не умею",
        "нет доступа",
        "не доступно",
    ]
    return any(b in rl for b in bad)


# =========================
# PERSONA PHOTO STORAGE API
# =========================
def _load_persona_index() -> dict:
    if not PERSONA_INDEX.exists():
        return {"photos": []}
    try:
        return json.loads(PERSONA_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"photos": []}


def _save_persona_index(d: dict) -> None:
    PERSONA_INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def add_persona_photo_bytes(chat_id: int, user_id: int, data: bytes, ext: str = "jpg", note: str = "") -> str:
    ts = int(_now())
    safe_ext = (ext or "jpg").lower().replace(".", "")
    name = f"vesya_{chat_id}_{user_id}_{ts}.{safe_ext}"
    path = PERSONA_DIR / name
    path.write_bytes(data)

    idx = _load_persona_index()
    photos = idx.get("photos") or []
    photos.append({"name": name, "path": str(path), "ts": ts, "note": (note or "")[:200]})
    idx["photos"] = photos[-50:]
    _save_persona_index(idx)
    return name


def _last_photo_meta_path(chat_id: int, user_id: int) -> Path:
    return LAST_PHOTO_DIR / f"{chat_id}_{user_id}.json"


def note_last_user_photo(chat_id: int, user_id: int, photo_path: str) -> None:
    meta = {
        "chat_id": int(chat_id),
        "user_id": int(user_id),
        "photo_path": str(photo_path),
        "expires_at": _now() + LAST_PHOTO_TTL_SEC,
    }
    _last_photo_meta_path(chat_id, user_id).write_text(json.dumps(meta), encoding="utf-8")


def pop_last_user_photo(chat_id: int, user_id: int) -> Optional[str]:
    p = _last_photo_meta_path(chat_id, user_id)
    if not p.exists():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if _now() > float(meta.get("expires_at") or 0):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
    path = (meta.get("photo_path") or "").strip()
    return path or None


# =========================
# OPENAI HELPERS
# =========================
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
                    if isinstance(txt, str) and txt:
                        chunks.append(txt)
        if chunks:
            return "".join(chunks).strip()

    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _sanitize_reply(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"```(?:json)?", "", t, flags=re.IGNORECASE).strip()
    t = t.replace("```", "").strip()
    if len(t) > 700:
        t = t[:700].rstrip() + "…"
    return t


# =========================
# MAIN DECIDE
# =========================
_SYSTEM_PROMPT = """Ты — Веся, телеграм-бот ассистент. Ты НЕ исполняешь действия сам: только определяешь intent и коротко отвечаешь пользователю.
Доступные intent: chat, news, content, end.

Правила:
- Ответ должен быть коротким (1–2 предложения).
- Если intent = news или content, ответ должен быть подтверждением действия (ack), НЕ уточняющим вопросом и НЕ мета-комментарием про код/пайплайны/файлы.
- Если пользователь просит закончить или явно говорит "стоп/пока", intent=end.
- Если запрос — обычный разговор, intent=chat.

Верни JSON строго вида:
{"intent":"chat|news|content|end","reply":"текст"}
"""


def decide(chat_id: int, user_id: int, text: str) -> DialogDecision:
    text = (text or "").strip()

    add_user(chat_id, user_id, text)
    touch(chat_id, user_id)

    # Fast ping
    if _looks_like_ping(text):
        reply = random.choice(PING_ANSWERS)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    # End
    if END_HINT_RE.search(text):
        add_assistant(chat_id, user_id, "ок")
        return DialogDecision(intent="end", reply="ок")

    # Hard news/content routing (NO model)
    if NEWS_HINT_RE.search(text):
        reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="news", reply=reply)

    if CONTENT_HINT_RE.search(text):
        reply = _deterministic_pick(_ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="content", reply=reply)

    # If no OpenAI key -> clarify
    if not _has_key():
        reply = _pick_clarify(chat_id, user_id, text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    # Model routing
    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
        )

        out = _extract_text(resp)
        _dbg(f"raw model out: {out[:220].replace(chr(10), ' ')}")

        data: Optional[dict] = None
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
            # if model ignored JSON contract, treat as chat text, but block meta-pipeline crap
            reply = _sanitize_reply(out)
            if _looks_like_meta_pipeline(reply):
                reply = ""
            if not reply:
                reply = _pick_clarify(chat_id, user_id, text)
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="chat", reply=reply)

        intent = (str(data.get("intent", "chat")) or "chat").strip().lower()
        if intent not in {"chat", "news", "content", "end"}:
            intent = "chat"

        reply = _sanitize_reply(str(data.get("reply", "")))

        # Guardrail: action intents MUST NOT be clarification or meta-pipeline talk
        if intent == "news" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply) or not reply):
            reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{text}")
        elif intent == "content" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply) or not reply):
            reply = _deterministic_pick(_ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{text}")
        else:
            # For chat: never show meta-pipeline talk
            if _looks_like_meta_pipeline(reply):
                reply = ""

        if not reply:
            reply = _pick_clarify(chat_id, user_id, text)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"decide EXC: {type(e).__name__}: {e}")
        reply = _pick_clarify(chat_id, user_id, text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)


# =========================
# PHOTO: describe/compare (kept compatible)
# =========================
def describe_or_compare_photo(text: str, img_bytes: bytes) -> Optional[DialogDecision]:
    """
    If your main.py uses this, keep behavior conservative:
    - do NOT identify real people
    - only allow safe generic description or deny "is it you" prompts
    """
    try:
        t = (text or "").strip()
        tl = t.lower()

        # "is it you" -> deny hard
        if IS_IT_YOU_RE.search(tl):
            reply = random.choice(DENY_ME_HARD)
            return DialogDecision(intent="chat", reply=reply)

        # If no key, just refuse gracefully
        if not _has_key():
            return DialogDecision(intent="chat", reply="вижу фото. что сделать: описать или сравнить с прошлым?")

        client = OpenAI()

        # Use vision model: ask for short safe description only
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        prompt = (
            "Опиши изображение кратко и безопасно: что на фото, окружение, одежда/объекты. "
            "НЕ пытайся идентифицировать человека, НЕ называй имён."
        )

        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {"role": "system", "content": "Ты — безопасный ассистент."},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_base64": b64},
                    ],
                },
            ],
        )

        out = _extract_text(resp)
        reply = _sanitize_reply(out) or "вижу фото. хочешь описание или сравнение?"
        return DialogDecision(intent="chat", reply=reply)

    except Exception as e:
        _dbg(f"vision EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="вижу фото. что делаем дальше?")
