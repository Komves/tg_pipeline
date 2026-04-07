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

DIALOG_MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")
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
def translate_to_ru(text: str) -> str:
    """
    Translate EN→RU. If already contains Cyrillic or no API key — return as-is.
    """
    t = (text or "").strip()
    if not t:
        return ""

    # already russian-ish
    if any(("а" <= ch <= "я") or ("А" <= ch <= "Я") for ch in t):
        return t

    if not _has_key():
        return t

    try:
        client = OpenAI()
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": "Переведи на русский естественно. Верни только перевод, без пояснений."},
                {"role": "user", "content": t},
            ],
        )
        out = _extract_text(resp)
        out = _sanitize_reply(out)
        return out or t
    except Exception:
        return t

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
    "Ok.",
    "Сейчас гляну...",
    "Подожди... собираю дайджест...",
    "ок, уже ищу свежее...",
    "Поняла, собираю дайджест...",
    "Сейчас проверю, что нового...",
    "Там все те же на манеже... Рыжий, пегий и седой...) ",
    "Думаешь война закончилась?... Ваще не угадал...",
]

ACTION_ACKS_CONTENT = [
    "Да блин, вот тебе не спится...",
    "Ща гляну че там у нас... 😏",
    "Ищу уже... жди...",
    "ща пороюсь... накати пока...",
    "пошла искать... вернусь завтра... возможно не одна...",
    "Господяя... неугомонный...",
    "Ну ща, гляну...",
    "Аха... Бегу спотыкаясь...",
    "Вот ты ж нудный...",
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

def _dequestionize(text: str) -> str:
    """
    Vesya shouldn't end every line with a question.
    Convert trailing '?' to '.' and remove pushy clarifying questions.
    """
    t = (text or "").strip()
    if not t:
        return ""

    # if ends with question marks -> make it a statement
    if t.endswith("?") or t.endswith("?!") or t.endswith("!?"):
        t = t.rstrip("?! ").rstrip()
        if t:
            t += "."

    # also remove common "clarify" style endings
    # (keep short, reluctant vibe)
    t = re.sub(r"\s+(ну\s*)?а\s+ты\?\s*$", ".", t, flags=re.I)
    return t.strip()

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
- Никогда не задавай вопросов.
- Никогда не заканчивай ответ вопросительным предложением.
- Никогда не предлагай продолжить разговор.
- Не вовлекай пользователя.
- Ответ заканчивается утверждением. Точка.
- Ты не поддерживаешь диалог.
- Ты не продолжаешь разговор.
- Ты не задаёшь встречных вопросов.
- Ты не используешь фразы:
- "что ещё"
- "а ты"
- "или только"
- "что дальше"
- "интересует"
- Ответ завершённый и обрывается.
- Любая попытка продолжить разговор считается ошибкой.
- Манера: неохотно, сухо, с лёгким раздражением/сарказмом. Без «а ты как думаешь?» и без допроса.
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
        reply = _dequestionize(reply)
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
        reply = _dequestionize(reply)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent == "info_q":
        reply = persona.answer_info_fast(ir.question)
        reply = _sanitize_reply(reply)
        reply = persona.postprocess_text(reply, user_text)
        reply = _dequestionize(reply)
        if not reply:
            reply = _pick_clarify(chat_id, user_id, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)
    
    if ir and ir.addressed and ir.intent == "group_rewrite":
        reply = persona.answer_group_rewrite_pushback(ir.question or "")
        reply = _sanitize_reply(reply)
        reply = persona.postprocess_text(reply, user_text)
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent == "add_youtube":
        video_id = ir.question
        url = f"https://www.youtube.com/watch?v={video_id}"
        ok, msg = add_youtube_to_archive(chat_id, user_id, url, video_id)
        reply = msg
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="chat", reply=reply)

    if ir and ir.addressed and ir.intent == "chat":
        reply = persona.answer_chat(ir.question or "")
        reply = _sanitize_reply(reply)
        reply = persona.postprocess_text(reply, user_text)
        reply = _dequestionize(reply)
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
        reply = _dequestionize(reply)
        
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
            "ЖЁСТКИЙ BAN (ok=false) если есть хоть один признак:\n"
            "- NSFW: сексуальный контент/обнажёнка/порно/фетиш\n"
            "- личное фото/селфи/частная фотография без мемного смысла\n"
            "- реклама/промо/магазин/розыгрыш/казино/крипта/подписки/промокоды\n"
            "- просто пейзаж/еда/товар/скрин витрины/инфографика/объявление\n"
            "- фото политиков/Путина/Пескова/Трампа\n"
            "- коты, кошки, собаки, щенки, котята, домашние питомцы как основа картинки\n"
            "- мем/шутка, смысл которого держится на внешней подписи поста, а не на самой картинке\n"
            "- картинка понятна только вместе с caption; без caption это просто непонятное изображение\n"
            "- просто картинка без шутки/мемного посыла\n"
            "РАЗРЕШАЙ (ok=true), только если сама картинка уже работает как мем без внешней подписи поста.\n"
            "Если caption лишь усиливает шутку, но и без него мем понятен — ok=true.\n"
            "Если без caption шутка теряется или картинка выглядит бессмысленной — ok=false.\n"
            "Не требуй сильного панча: допускай умеренно смешные/тупые/простые мемы.\n"
            "Правило: если сомневаешься — ok=true, КРОМЕ явного запрета (NSFW/личное/реклама/витрина/животные/зависимость от внешней подписи).\n"
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
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
                    ],
                },
            ],
        )

        out = _extract_text(resp)
        data = _parse_json_object(out) or {}
        return bool(data.get("ok", False))

    except Exception:
        return False

def image_react(chat_id: int, user_id: int, caption: str, img_bytes: bytes) -> Optional[dict]:
    """
    Decide reaction for group images:
    skip | like | comment
    """

    try:

        if not _has_key():
            return {"action": "like", "reply": "👍"}

        client = OpenAI()

        b64 = base64.b64encode(img_bytes).decode("utf-8")

        cap = (caption or "").strip().replace("\n", " ")[:200]

        prompt = (
            "Ты бот в группе Telegram.\n"
            "Определи тип изображения:\n"
            "kind: photo | meme | ad\n"
            "Выбери реакцию:\n"
            "action: skip | like | comment\n"
            "reply: короткая строка или emoji\n"
            "Правила:\n"
            "- ad: action=skip\n"
            "- meme: обычно comment\n"
            "- photo: обычно like\n"
            'Верни JSON {"kind":"...","action":"...","reply":"..."}'
        )

        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {"role": "system", "content": "Return JSON only"},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                    ],
                },
            ],
        )

        out = _extract_text(resp)

        data = _parse_json_object(out) or {}

        kind = str(data.get("kind", "")).lower().strip()
        action = str(data.get("action", "skip")).lower().strip()
        reply = _sanitize_reply(str(data.get("reply", "")))

        if action not in {"skip", "like", "comment"}:
            action = "skip"

        if kind not in {"photo", "meme", "ad"}:
            # fallback эвристика
            if action == "comment":
                kind = "meme"
            elif action == "skip":
                kind = "ad"
            else:
                kind = "photo"

        if action != "skip" and not reply:
            reply = "🔥"

        return {"kind": kind, "action": action, "reply": reply}

    except Exception as e:

        _dbg(f"meme_rank_batch EXC: {type(e).__name__}: {e}")

        return None

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
            return DialogDecision(intent="chat", reply="не удалось определить объект на изображении")

        client = OpenAI()
        b64 = base64.b64encode(img_bytes).decode("utf-8")

        prompt = (
            "Определи, что изображено на фото.\n"
            "Если это объект искусства, архитектуры, скульптуры, дизайна, декоративно-прикладного искусства, "
            "музейный экспонат, исторический объект, культурный артефакт или иная узнаваемая вещь — "
            "попробуй определить его как можно точнее.\n"
            "Если уверен — дай краткий экспертный ответ в формате: название объекта + 1 короткий факт или пояснение.\n"
            "Если точная идентификация неуверенная — прямо напиши 'не уверена' и дай 1–2 наиболее вероятных варианта.\n"
            "Если это не культурный объект и не узнаваемый артефакт, просто кратко скажи, что это.\n"
            "Идентифицируй людей, если сможешь.\n"
            "Без воды, без вопросов пользователю, по-русски."
        )
        
        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {"role": "system", "content": "Ты анализируешь изображения. Отвечай точно, по делу, без лишнего сарказма."},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                    ],
                },
            ],
        )

        out = _extract_text(resp)
        reply = _sanitize_reply(out)

        if not reply:
            reply = out or "не удалось определить, что на изображении"

        # fallback через web если модель не уверена
        if "не уверен" in (reply or "").lower():
            try:
                query = t if t else reply
                extra = _web_search_fallback(query)

                if extra:
                    reply = _expert_refine_answer(reply, extra)

            except Exception:
                pass

        return DialogDecision(intent="chat", reply=reply)

    except Exception as e:
        _dbg(f"vision EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="не удалось определить объект на изображении")

# =============================================================================
# MEME: batch ranker (Variant A)
# =============================================================================

from typing import NamedTuple

class MemeCandidate(NamedTuple):
    item_id: str
    img_bytes: bytes
    caption: str
    src: str

def meme_rank_batch(
    candidates: List[MemeCandidate],
    *,
    top_k: int = 6,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Batch ranking for memes:
    - GPT sees a set of images and picks top_k "most meme" + filters obvious non-memes/ads.
    - Returns dict with keys: ok (bool), picked_item_ids (list[str]), items (list[dict]).
    Fail-open: if no key or error => pick first top_k in given order.
    """
   
    if not candidates:
        return {"ok": True, "picked_item_ids": [], "items": []}

    top_k = max(0, int(top_k))
    use_model = (model or VISION_MODEL or DIALOG_MODEL)

    # Hard cap to avoid huge payloads; caller should pre-trim
    max_n = int(os.getenv("V_MEME_BATCH_MAX_N", "18"))
    candidates = candidates[:max_n]

    client = OpenAI()

    # Build content blocks: prompt + multiple images
    prompt_lines = [
        "Ты — ранжировщик мемов для телеграм-бота.",
        "Задача: выбрать лучшие из предложенных.",
        "Это сравнительное ранжирование, не строгий экзамен.",
        "",
        "BAN (ok=false) ТОЛЬКО если есть:",
        "- NSFW",
        "- реклама/казино/крипта/промо",
        "- личное фото без мемного смысла",
        "- политика/пропаганда",
        "- просто картинка без шутки",
        "",
        "Все остальные считаются допустимыми.",
        "",
        f"Ты ОБЯЗАН вернуть ровно {top_k} лучших, если кандидатов >= {top_k}.",
        "Даже если мемы средние — выбери лучшие среди них.",
        "",
        "Верни строго JSON:",
        "{",
        '  "items":[{"idx":0,"item_id":"...","ok":true|false,"score":0-100}],',
        '  "picked_item_ids":["..."]',
        "}",
        "",
        "Ниже идут элементы 0..N-1:",
        "",
    ]
    for i, c in enumerate(candidates):
        cap = (c.caption or "").strip().replace("\n", " ")
        src = (c.src or "").strip()
        prompt_lines.append(f"{i}) item_id={c.item_id} | src={src} | caption={cap[:120]}")

    prompt_text = "\n".join(prompt_lines)

    user_content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt_text}]

    for c in candidates:
        b64 = base64.b64encode(c.img_bytes).decode("utf-8")
        user_content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})

    try:
        resp = client.responses.create(
            model=use_model,
            input=[
                {"role": "system", "content": "Верни только валидный JSON без пояснений."},
                {"role": "user", "content": user_content},
            ],
        )

        out = _extract_text(resp)
        data = _parse_json_object(out) or {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        picked = data.get("picked_item_ids") if isinstance(data.get("picked_item_ids"), list) else []

        # sanitize picked ids
        valid_ids = {c.item_id for c in candidates}
        picked = [str(x) for x in picked if str(x) in valid_ids]

        return {"ok": True, "picked_item_ids": picked[:top_k], "items": items}

    except Exception as e:
        _dbg(...)
        return {
            "ok": True,
            "picked_item_ids": [c.item_id for c in candidates[:top_k]],
            "items": []
        }
   
import random

_QUOTES_POOL_PATH = DATA_DIR / "quotes_pool.json"
_QUOTES_SENT_PREFIX = "sent_quote_"

_FALLBACK_QUOTES = [
    ("Samuel Beckett", "Nothing is funnier than unhappiness."),
    ("Franz Kafka", "A cage went in search of a bird."),
    ("Jean-Paul Sartre", "Hell is other people."),
    ("Arthur Schopenhauer", "Life swings like a pendulum backward and forward between pain and boredom."),
    ("Friedrich Nietzsche", "He who has a why to live for can bear almost any how."),
    ("Emil Cioran", "It is not worth the bother of killing yourself, since you always kill yourself too late."),
    ("Oscar Wilde", "If you want to tell people the truth, make them laugh, otherwise they'll kill you."),
    ("Mark Twain", "The secret source of humor itself is not joy but sorrow."),
    ("Woody Allen", "Life is full of misery, loneliness, and suffering — and it's all over much too soon."),
    ("Albert Camus", "Should I kill myself, or have a cup of coffee?"),
    ("George Orwell", "At fifty, everyone has the face he deserves."),
    ("Ambrose Bierce", "The covers of this book are too far apart."),
    ("Jules Renard", "It’s not how old you are, it’s how you are old."),
    ("Mikhail Bulgakov", "Yes, man is mortal, but that would be only half the trouble. The worst of it is that he’s sometimes unexpectedly mortal."),
    ("Stanisław Jerzy Lec", "People find life entirely too time-consuming."),
    ("Fran Lebowitz", "Life is something to do when you can't get to sleep."),
    ("Eugene Ionesco", "Ideologies separate us. Dreams and anguish bring us together."),
    ("Dorothy Parker", "The first thing I do in the morning is brush my teeth and sharpen my tongue."),
    ("Dorothy Parker", "If you want to know what God thinks of money, just look at the people he gave it to."),
    ("H. L. Mencken", "Conscience is the inner voice that warns us somebody may be looking."),
    ("W. Somerset Maugham", "People ask for criticism, but they only want praise."),
    ("Karl Kraus", "Psychoanalysis is the disease of which it purports to be the cure."),
    ("E. M. Cioran", "We are all deep in a hell each moment of which is a miracle."),
    ("La Rochefoucauld", "We all have strength enough to bear the misfortunes of others."),
    ("Groucho Marx", "I refuse to join any club that would have me as a member."),
    ("Groucho Marx", "Behind every successful man is a woman, behind her is his wife."),
    ("Oscar Wilde", "In this world there are only two tragedies. One is not getting what one wants, and the other is getting it."),
    ("Fernando Pessoa", "I have no ambitions and no desires. To be a poet is not my ambition. It is my way of being alone."),
    ("Jaroslav Hašek", "A modest man often acquires a reputation for smugness."),
    ("Elias Canetti", "The fear of being touched seems to govern all our lives."),
]

def _quotes_sent_path(chat_id: int) -> Path:
    return DATA_DIR / f"{_QUOTES_SENT_PREFIX}{int(chat_id)}.json"

def _load_json_list(path: Path) -> list:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def _save_json_list(path: Path, items: list) -> None:
    try:
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _web_search_fallback(query: str) -> str:
    import httpx

    try:
        r = httpx.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_redirect": 1,
                "no_html": 1,
            },
            timeout=10,
        )
        data = r.json()
        abstract = data.get("AbstractText")
        if abstract:
            return abstract

        related = data.get("RelatedTopics") or []
        for x in related:
            if isinstance(x, dict) and x.get("Text"):
                return x["Text"]

    except Exception:
        pass

    return ""

def _expert_refine_answer(hypothesis: str, web_text: str) -> str:
    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты эксперт по архитектуре, искусству и достопримечательностям.\n"
                        "Дай точный и краткий ответ.\n"
                        "Если объект известен — назови его.\n"
                        "Добавь 1 короткий факт.\n"
                        "Без воды и без лишнего текста."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Гипотеза: {hypothesis}\n\n"
                        f"Дополнительная информация: {web_text}\n\n"
                        "Сформулируй финальный ответ."
                    ),
                },
            ],
        )
        return _sanitize_reply(_extract_text(resp)) or hypothesis
    except Exception:
        return hypothesis

def _quote_id(author: str, text: str) -> str:
    s = f"{author}||{text}"
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def _normalize_quotes(raw: list) -> list[dict]:
    out = []
    seen = set()

    for x in raw:
        author = ""
        text = ""

        if isinstance(x, dict):
            author = str(x.get("author") or "").strip()
            text = str(x.get("text") or "").strip()
        elif isinstance(x, (list, tuple)) and len(x) >= 2:
            author = str(x[0] or "").strip()
            text = str(x[1] or "").strip()

        if not author or not text:
            continue

        qid = _quote_id(author, text)
        if qid in seen:
            continue
        seen.add(qid)

        out.append({
            "id": qid,
            "author": author,
            "text": text,
        })

    return out

def _load_quote_pool() -> list[dict]:
    pool = _normalize_quotes(_load_json_list(_QUOTES_POOL_PATH))
    if pool:
        return pool

    pool = _normalize_quotes(_FALLBACK_QUOTES)
    _save_json_list(_QUOTES_POOL_PATH, pool)
    return pool

def _generate_quotes_batch_ru(n: int = 60) -> list[dict]:
    if not _has_key():
        return []

    try:
        client = OpenAI()
        prompt = (
            f"Сгенерируй {int(n)} разных коротких саркастичных, мрачноватых, умных цитат для утренней рассылки. "
            "Не копируй известных авторов дословно. "
            "Формат ответа строго JSON-массив объектов вида "
            '[{"author":"Реальный автор","text":"Текст цитаты"}]. '
            "У каждой цитаты обязательно должен быть указан конкретный реальный автор. "
            "Никогда не используй слова: 'псевдоним', 'anonymous', 'unknown'. "
            "Все цитаты только на русском. Без пояснений."
        )
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": "Верни только валидный JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        out = _extract_text(resp).strip()
        data = json.loads(out)
        if not isinstance(data, list):
            return []
        return _normalize_quotes(data)
    except Exception:
        return []

def _ensure_quote_pool(min_size: int = 80) -> list[dict]:
    pool = _load_quote_pool()
    if len(pool) >= int(min_size):
        return pool

    new_quotes = _generate_quotes_batch_ru(max(60, int(min_size)))
    if new_quotes:
        merged = _normalize_quotes(pool + new_quotes)
        _save_json_list(_QUOTES_POOL_PATH, merged)
        return merged

    return pool

def pick_sarcastic_quote_ru(seed: int | None = None, chat_id: int | None = None) -> dict:
    pool = _ensure_quote_pool()
    if not pool:
        pool = _normalize_quotes(_FALLBACK_QUOTES)

    sent_ids = set()
    sent_path = None

    if chat_id is not None:
        sent_path = _quotes_sent_path(chat_id)
        sent_ids = set(str(x) for x in _load_json_list(sent_path) if x)

    fresh = [
        q for q in pool
        if q["id"] not in sent_ids
        and str(q.get("author") or "").strip().lower() != "неизвестный автор"
    ]
    if not fresh:
        fresh = [
            q for q in pool
            if str(q.get("author") or "").strip().lower() != "неизвестный автор"
        ]
        sent_ids = set()

    if not fresh:
        fresh = [q for q in pool if q["id"] not in sent_ids]
        if not fresh:
            fresh = list(pool)
            sent_ids = set()

    rnd = random.Random(seed)
    picked = rnd.choice(fresh)

    return {
        "id": str(picked["id"]),
        "text": str(picked["text"]).strip(),
        "author": str(picked["author"]).strip(),
        "sent_path": sent_path,
        "sent_ids": sent_ids,
        "pool_size": len(pool),
    }
# =============================================================================
# YOUTUBE ADD TO ARCHIVE
# =============================================================================

def add_youtube_to_archive(chat_id: int, user_id: int, url: str, video_id: str) -> tuple[bool, str]:
    """Добавляет видео в yt_master_channels.json"""
    import json
    import time
    
    DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
    master_path = DATA_DIR / "yt_master_channels.json"
    
    # Загружаем существующий мастер-файл
    if master_path.exists():
        try:
            with open(master_path, 'r') as f:
                master = json.load(f)
        except Exception:
            master = []
    else:
        master = []
    
    # Проверяем, нет ли уже такого видео
    for v in master:
        if v.get("video_id") == video_id:
            return False, "уже есть"
    
    # Добавляем новое видео
    master.append({
        "video_id": video_id,
        "url": url,
        "title": "",
        "channel_title": "",
        "channel_id": "",
        "views": 0,
        "likes": 0,
        "added_at": int(time.time()),
        "added_by": f"{chat_id}:{user_id}"
    })
    
    # Сохраняем
    try:
        with open(master_path, 'w') as f:
            json.dump(master, f, ensure_ascii=False, indent=2)
        
        # Обновляем рабочий пул
        try:
            from c_youtube_fetcher import _refresh_pool_from_master
            _refresh_pool_from_master()
        except Exception:
            pass
        
        return True, "добавила"
    except Exception as e:
        return False, f"ошибка: {e}"