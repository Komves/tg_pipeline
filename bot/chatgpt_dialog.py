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

import persona

# =============================================================================
# CONFIG
# =============================================================================

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Accept both legacy and new env names
DIALOG_TTL_SEC = int(os.getenv("V_DIALOG_TTL_SEC", os.getenv("DIALOG_TTL_SEC", "3600")))
DIALOG_MAX_TURNS = int(os.getenv("V_DIALOG_MAX_TURNS", os.getenv("DIALOG_MAX_TURNS", "30")))

DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", os.getenv("DIALOG_MODEL", "gpt-4o-mini"))
VISION_MODEL = os.getenv("V_VISION_MODEL", DIALOG_MODEL)

DEBUG_DIALOG = (os.getenv("V_DIALOG_DEBUG", os.getenv("DEBUG_DIALOG", "0")) or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Last-photo TTL (used by main.py photo flow)
LAST_PHOTO_TTL_SEC = int(os.getenv("V_LAST_PHOTO_TTL_SEC", "300"))
_LAST_PHOTO_DIR = DATA_DIR / "vesya_last_photo"
_LAST_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

# Persona photo storage (optional; safe to keep)
PERSONA_INDEX = DATA_DIR / "persona_index.json"
_PERSONA_PHOTOS_DIR = DATA_DIR / "persona_photos"
_PERSONA_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# TYPES
# =============================================================================


@dataclass
class DialogDecision:
    intent: str  # chat | news | content | end
    reply: str


# =============================================================================
# IN-MEM DIALOG SESSIONS (used for "active" mode after name / photo)
# =============================================================================


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True
    last_clarify_idx: Optional[int] = None


_sessions: Dict[Tuple[int, int], _Session] = {}


def _now() -> float:
    return time.time()


def _dbg(msg: str) -> None:
    if DEBUG_DIALOG:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        print(f"[chatgpt_dialog] {now} {msg}", flush=True)


def _has_key() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def _gc() -> None:
    now = _now()
    dead = [k for k, s in _sessions.items() if s.expires_at < now]
    for k in dead:
        _sessions.pop(k, None)


def activate(chat_id: int, user_id: int) -> None:
    _gc()
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


def is_active(chat_id: int, user_id: int) -> bool:
    s = _sessions.get((chat_id, user_id))
    return bool(s and s.active and s.expires_at >= _now())


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


# =============================================================================
# LAST PHOTO (main.py uses these)
# =============================================================================


def _last_photo_meta_path(chat_id: int, user_id: int) -> Path:
    return _LAST_PHOTO_DIR / f"{chat_id}_{user_id}.json"


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

    # expire
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


# =============================================================================
# PERSONA PHOTO INDEX (optional)
# =============================================================================


def _load_persona_index() -> dict:
    if not PERSONA_INDEX.exists():
        return {}
    try:
        return json.loads(PERSONA_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_persona_index(data: dict) -> None:
    try:
        PERSONA_INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def save_persona_photo(chat_id: int, user_id: int, photo_bytes: bytes, ext: str = "jpg", note: str = "") -> str:
    ts = int(_now())
    ext = (ext or "jpg").lstrip(".").lower()
    fn = f"{chat_id}_{user_id}_{ts}.{ext}"
    path = _PERSONA_PHOTOS_DIR / fn
    path.write_bytes(photo_bytes)

    idx = _load_persona_index()
    key = f"{chat_id}:{user_id}"
    rec = idx.get(key, {})
    rec["last_photo"] = str(path)
    rec["last_ts"] = ts
    if note:
        rec["note"] = note
    idx[key] = rec
    _save_persona_index(idx)
    return str(path)


def get_last_persona_photo_path(chat_id: int, user_id: int) -> Optional[str]:
    idx = _load_persona_index()
    key = f"{chat_id}:{user_id}"
    rec = idx.get(key, {})
    p = rec.get("last_photo")
    if p and Path(p).exists():
        return p
    return None


# =============================================================================
# SEMANTIC GUARDRAILS
# =============================================================================

_CLARIFY_TOKENS = [
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

_META_PIPELINE_TOKENS = [
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


def _looks_like_clarification(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    rl = r.lower()
    if "?" in r:
        return True
    return any(t in rl for t in _CLARIFY_TOKENS)


def _looks_like_meta_pipeline(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    rl = r.lower()
    return any(t in rl for t in _META_PIPELINE_TOKENS)


def _deterministic_index(n: int, seed_str: str) -> int:
    if n <= 0:
        return 0
    import hashlib

    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % n


def _deterministic_pick(options: List[str], seed_str: str) -> str:
    if not options:
        return ""
    return options[_deterministic_index(len(options), seed_str)]


# =============================================================================
# REPLIES
# =============================================================================

_ACTION_ACKS_NEWS = [
    "Ну щас, соберу ваши так сказать новости...",
    "сейчас гляну...",
    "Ожидай... утырок... собираю типа дайджест...",
    "ок, уже ищу свежее...",
    "поняла, собираю дайджест...",
    "сейчас проверю, что нового...",
    "вот там все те же на арене... оно тебе надо?"
    "Думаешь война закночилась?... Не угадал..."
    "Новости... новости... а депутаты п.доры!"
    "Вот вообще там ничего умного... Ну ладно, ожидайте..."
    "Новости??? Пошла читать... блин башка болит пипец..."
]

ACTION_ACKS_CONTENT = [
    "...................................  Ай... блять, ни на ту кнопку нажала..."
    "Да блин, вот тебе не спится...",
    "Ща гляну че там у нас... 😏",
    "Ищу уже... жди...",
    "ща пороюсь... накати пока...",
    "пошла искать... вернусь завтра... возможно не одна...",
    "Господяя... неугомонный...",
    "Ну ща, гляну...",
    "Аха... Бегу спотыкаясь...",
    "Слушай, отъебись уже а.... Вот не до тебя щас... Ну ладно поищу...",
    "Может лучше стриптиз?... Но ты все равно не увидишь... Так что только картинки с видосиками..."
    "Ты задрал уже... сколько можно...?"
    "Вот ты ж нудный какой..."
]

def _pick_clarify(chat_id: int, user_id: int, user_text: str) -> str:
    # Use persona.CHOICE_PROMPTS if present, but keep variation deterministic + no immediate repeat.
    prompts = getattr(persona, "CHOICE_PROMPTS", None) or [
        "контент или новости?",
        "что делаем: контент / новости?",
        "ну и? контент или новости?",
    ]
    s = _sessions.get((chat_id, user_id))
    seed = f"clarify:{chat_id}:{user_id}:{(user_text or '').strip().lower()}"
    idx = _deterministic_index(len(prompts), seed)

    if s and s.last_clarify_idx is not None and idx == s.last_clarify_idx and len(prompts) > 1:
        idx = (idx + 1) % len(prompts)
    if s:
        s.last_clarify_idx = idx
    return prompts[idx]


# =============================================================================
# OPENAI JSON PARSING (fallback)
# =============================================================================

def _extract_text(resp: Any) -> str:
    try:
        return (getattr(resp, "output_text", "") or "").strip()
    except Exception:
        pass

    try:
        out = []
        for item in resp.output:
            if getattr(item, "type", "") == "message":
                for c in item.content:
                    if getattr(c, "type", "") == "output_text":
                        out.append(getattr(c, "text", ""))
        return "".join(out).strip()
    except Exception:
        pass

    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _sanitize_reply(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"```(?:json)?", "", t, flags=re.I).strip()
    t = t.replace("```", "").strip()
    if len(t) > 700:
        t = t[:700].rstrip() + "…"
    return t


def _parse_json_object(s: str) -> Optional[dict]:
    if not s:
        return None
    s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# =============================================================================
# SYSTEM PROMPT (persona + contract)
# =============================================================================

_SYSTEM_PROMPT = (
    getattr(persona, "_SYSTEM_PROMPT", "").strip()
    + "\n\n"
    + """Ты НЕ исполняешь действия сам: только определяешь intent и коротко отвечаешь пользователю.
Доступные intent: chat, news, content, end.

Правила:
- Ответ 1–3 строки.
- Если intent = news или content, ответ должен быть подтверждением действия (ack), НЕ уточняющим вопросом и НЕ мета-комментарием про код/пайплайны/файлы.
- Если пользователь просит закончить или явно говорит "стоп/пока", intent=end.
- Если запрос — обычный разговор, intent=chat.

Верни JSON строго вида:
{"intent":"chat|news|content|end","reply":"текст"}
"""
)


# =============================================================================
# PUBLIC API
# =============================================================================

def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    """
    Dialog layer: intent + short reply. No sending, no pipeline execution.
    main.py will execute pipelines based on intent.
    """
    user_text = (user_text or "").strip()
    intent = "chat"
    reply = ""
    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)    
    tl = user_text.lower()
    if ("веся" in tl or "веслава" in tl) and ("новост" in tl or "дайджест" in tl):
        reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="news", reply=reply)

    # 0) Fast routing using persona rules (restores character + stable behavior)
    try:
        ir = persona.detect_intent(user_text)
    except Exception:
        ir = None

    if ir and ir.addressed and ir.intent == "ping":
        reply = random.choice(getattr(persona, "PING_ANSWERS", ["я тут", "слушаю"]))
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent in {"alive_check"}:
        reply = random.choice(getattr(persona, "ALIVE_ANSWERS", ["я здесь"]))
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent in {"bot_q"}:
        # Never "я просто бот" — keep Vesya ambiguity
        reply = random.choice(getattr(persona, "BOT_Q_ANSWERS", ["а ты?"]))
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent in {"news"}:
        reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="news", reply=reply)

    # IMPORTANT: "жги/огня/ignite" must start content run (user requirement)
    if ir and ir.addressed and ir.intent in {"ignite_choice", "run_all"}:
        reply = _deterministic_pick(ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="content", reply=reply)

    if ir and ir.addressed and ir.intent == "unclear":
        reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent == "info_q":
        reply = persona.answer_info_fast(ir.question)
        reply = _sanitize_reply(reply)
        reply = persona.postprocess_text(reply, user_text)
        if not reply:
            reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent == "chat":
        reply = persona.answer_chat(ir.question or "")
        reply = _sanitize_reply(reply)
        reply = persona.postprocess_text(reply, user_text)
        if not reply:
            reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    # 1) If no key — just clarify (do NOT become generic assistant)
    if not _has_key():
        reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    # 2) Model-based decision (fallback for non-addressed active sessions etc.)
    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
        )
        out = _extract_text(resp)
        _dbg(f"raw model out: {out[:220].replace(chr(10),' ')}")

        data = _parse_json_object(out)
        if not isinstance(data, dict):
            reply = _sanitize_reply(out)
            if _looks_like_meta_pipeline(reply):
                reply = ""
            if not reply:
                reply = _pick_clarify(chat_id, user_id, user_text)
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="chat", reply=reply)

        intent = (str(data.get("intent", "chat")) or "chat").strip().lower()
        if intent not in {"chat", "news", "content", "end"}:
            intent = "chat"

        reply = _sanitize_reply(str(data.get("reply", "")))

        # Guardrails for action intents
        if intent == "news" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply) or not reply or reply.strip().lower() == "ack"):
            reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
        elif intent == "content" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply) or not reply or reply.strip().lower() == "ack"):
            reply = _deterministic_pick(ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")
        else:
            if _looks_like_meta_pipeline(reply):
                reply = ""

        if not reply:
            reply = _pick_clarify(chat_id, user_id, user_text)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"decide EXC: {type(e).__name__}: {e}")
        reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

def meme_should_send(img_bytes: bytes, caption: str = "", src: str = "") -> bool:
    try:
        if not _has_key():
            return True

        client = OpenAI()
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        cap = (caption or "").strip()
        s = (src or "").strip()

        prompt = (
            "Ты фильтр контента для телеграм-бота. Реши, можно ли отправлять картинку как мем.\n"
            "Отклоняй (ok=false), если это реклама/промо/магазин/розыгрыш/казино/крипта/подписки/промокоды,\n"
            "или личное фото/селфи/частная фотография без мемного смысла,\n"
            "или просто пейзаж/еда/товар/скрин витрины/инфографика/объявление,\n"
            "или просто картинка без шутки/мемного посыла.\n"
            "Разрешай (ok=true), если это мем: есть шутка/ирония/сарказм/контекст (обычно текст на картинке или узнаваемая мемная подача).\n"
            "Верни строго JSON: {\"ok\":true|false}.\n"
            f"src={s}\n"
            f"caption={cap}\n"
        )

        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {"role": "system", "content": "Верни только валидный JSON без пояснений."},
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
        data = _parse_json_object(out) or {}
        return bool(data.get("ok", True))

    except Exception:
        return True

# =============================================================================
# PHOTO: describe/compare (main.py may call this)
# =============================================================================

def describe_or_compare_photo(text: str, img_bytes: bytes) -> Optional[DialogDecision]:
    """
    Safe vision helper:
    - Never identify people
    - Keep Vesya character via persona prompt
    """
    try:
        t = (text or "").strip()
        tl = t.lower()

        # If asked identity-ish -> answer in Vesya style (no confirmations)
        try:
            if getattr(persona, "IDENTITY_Q", None) and persona.IDENTITY_Q.search(tl):
                reply = persona.answer_info_fast(t)
                reply = _sanitize_reply(reply) or "зачем тебе?"
                return DialogDecision(intent="chat", reply=reply)
        except Exception:
            pass

        if not _has_key():
            return DialogDecision(intent="chat", reply="вижу фото. описать или сравнить?")

        client = OpenAI()
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        prompt = (
            "Опиши изображение кратко и безопасно: что на фото, окружение, одежда/объекты. "
            "НЕ пытайся идентифицировать человека, НЕ называй имён."
        )

        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {"role": "system", "content": getattr(persona, "_SYSTEM_PROMPT", "Ты — безопасный ассистент.")},
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
