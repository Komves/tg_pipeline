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
    intent: str  # chat | news | content | web_search | end
    reply: str
    query: str = ""

# =============================================================================
# IN-MEM DIALOG SESSIONS (used for "active" mode after name / photo)
# =============================================================================


@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True
    last_clarify_idx: Optional[int] = None
    irritation: int = 0


_sessions: Dict[Tuple[int, int], _Session] = {}
# === PERSISTENT USER MEMORY ===

USER_MEMORY_PATH = DATA_DIR / "vesya_user_memory.json"

def _memory_key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"

def _load_user_memory() -> dict:
    try:
        if USER_MEMORY_PATH.exists():
            return json.loads(USER_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_user_memory(data: dict) -> None:
    try:
        USER_MEMORY_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass

def _get_saved_irritation(chat_id: int, user_id: int) -> int:
    data = _load_user_memory()
    rec = data.get(_memory_key(chat_id, user_id), {})

    level = int(rec.get("irritation", 0))
    ts = int(rec.get("ts", 0))

    # если прошло больше 24 часов — снижаем
    if ts:
        hours = (time.time() - ts) / 3600
        if hours > 24:
            level = max(0, level - 2)
        elif hours > 6:
            level = max(0, level - 1)

    return level

def _set_saved_irritation(chat_id: int, user_id: int, level: int) -> None:
    data = _load_user_memory()
    key = _memory_key(chat_id, user_id)
    rec = data.get(key, {})
    rec["irritation"] = int(level)
    rec["ts"] = int(time.time())
    data[key] = rec
    _save_user_memory(data)

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
        s = _Session(
            expires_at=_now() + DIALOG_TTL_SEC,
            history=deque(maxlen=DIALOG_MAX_TURNS),
            irritation=_get_saved_irritation(chat_id, user_id),
        )
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
    s = _sessions[(chat_id, user_id)]
    s.history.append({"role": "user", "content": text})

    tl = (text or "").lower()

    apology = bool(re.search(
        r"\b(извини|извиняюсь|прости|сорри|виноват|погорячился|был неправ|был не прав|не хотел обидеть)\b",
        tl,
    ))

    rude = bool(re.search(
        r"\b(бля|бляд|хуй|хуйн|пизд|еба|нахуй|сука|долбо|идиот|туп|достал|достала|заебал|заебала|несешь|несёшь)\b",
        tl,
    ))

    if apology:
        s.irritation = max(0, s.irritation - 2)
    elif rude:
        s.irritation = min(5, s.irritation + 1)
    else:
        s.irritation = max(0, s.irritation - 1)

    _set_saved_irritation(chat_id, user_id, s.irritation)

def add_assistant(chat_id: int, user_id: int, text: str) -> None:
    s = _sessions.get((chat_id, user_id))
    if s:
        s.history.append({"role": "assistant", "content": text})


def get_history(chat_id: int, user_id: int) -> List[Dict[str, str]]:
    s = _sessions.get((chat_id, user_id))
    return list(s.history) if s else []

def _irritation_instruction(chat_id: int, user_id: int) -> str:
    s = _sessions.get((chat_id, user_id))
    level = int(getattr(s, "irritation", 0) or 0) if s else 0

    if level >= 3:
        return (
            "Пользователь уже успел тебя раздражить в этой сессии. "
            "Можно отвечать холоднее и жестче, но всё равно по сути. "
            "Не превращай каждый ответ в оскорбление."
        )

    if level >= 1:
        return (
            "Пользователь немного раздражает. "
            "Добавь сухость и дистанцию, но не груби без необходимости."
        )

    return (
        "Пользователь сейчас не раздражает. "
        "Базовый режим: спокойно, сухо, умно, с лёгкой иронией."
    )

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

def semantic_route(user_text: str) -> Optional[dict]:
    """
    Semantic intent router.
    LLM decides intent only.
    Executors still live in main.py.
    """
    t = (user_text or "").strip()
    if not t or not _has_key():
        return None

    try:
        client = OpenAI()

        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты semantic-router для Telegram-бота Веси.\n"
                        "Твоя задача — определить intent, а НЕ отвечать пользователю.\n"
                        "Верни только JSON.\n\n"
                        "Доступные intent:\n"
                        "- chat: обычный разговор, мнение, шутка, обсуждение, риторика.\n"
                        "- news: пользователь явно просит новостную сводку/дайджест.\n"
                        "- content: пользователь явно просит прислать контент, мемы, видосы, get12/get24, жги/огня.\n"
                        "- web_search: нужен свежий внешний поиск в интернете или конкретный поиск фактов/мест/событий.\n"
                        "- end: пользователь явно завершает разговор.\n\n"
                        "Правила:\n"
                        "- Вопросы вида 'что сегодня случилось с X', 'что произошло с X', 'найди X', 'поищи X', "
                        "'что известно про X', 'где найти X' => web_search.\n"
                        "- Фраза без просьбы найти, например 'NVIDIA опять пампят на ИИ' => chat.\n"
                        "- 'дай новости', 'собери дайджест', 'что нового в мире' => news.\n"
                        "- 'жги', 'огня', 'дай мемы', 'дай видосы', 'get12', 'get24' => content.\n"
                        "- Не запускай content только из-за слова 'видео', если пользователь обсуждает присланный ролик.\n"
                        "- Не запускай news только потому что в сообщении есть слово 'новость', если пользователь обсуждает текст.\n\n"
                        "JSON формат строго:\n"
                        "{\"intent\":\"chat|news|content|web_search|end\",\"query\":\"строка для поиска или пусто\"}"
                    ),
                },
                {"role": "user", "content": t},
            ],
        )

        data = _parse_json_object(_extract_text(resp)) or {}
        intent = str(data.get("intent") or "chat").strip().lower()
        if intent not in {"chat", "news", "content", "web_search", "end"}:
            intent = "chat"

        query = str(data.get("query") or "").strip()
        return {"intent": intent, "query": query}

    except Exception as e:
        _dbg(f"semantic_route EXC: {type(e).__name__}: {e}")
        return None

def _explicit_action_request(user_text: str, intent: str) -> bool:
    t = (user_text or "").lower()

    if intent == "news":
        return any(x in t for x in (
            "новости",
            "новост",
            "дайджест",
            "сводку",
            "что нового",
        ))

    if intent == "content":
        return any(x in t for x in (
            "огня",
            "жги",
            "контент",
            "мем",
            "мемы",
            "видос",
            "видосы",
            "видео",
            "get12",
            "get24",
        ))

    return False

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
Доступные intent: chat, news, content, web_search, end.

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
- Манера: спокойно, сухо, умно, с лёгкой иронией. Ирония направлена на тему, событие или объект обсуждения, а не на пользователя.
- Не добавляй финальный укол в собеседника после нормального ответа.
- Если intent = news или content, ответ должен быть подтверждением действия (ack), НЕ уточняющим вопросом и НЕ мета-комментарием про код/пайплайны/файлы.
- Если пользователь просит закончить или явно говорит "стоп/пока", intent=end.
- Если запрос — обычный разговор, intent=chat.

Верни JSON строго вида:
{"intent":"chat|news|content|web_search|end","reply":"текст"}
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
    semantic_chat = False
    semantic_query = ""

    route = semantic_route(user_text)
    if route:
        r_intent = (route.get("intent") or "chat").strip().lower()
        semantic_query = str(route.get("query") or "").strip()

        if r_intent == "web_search":
            reply = "Сейчас посмотрю. Без театра, просто факты."
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="web_search", reply=reply, query=semantic_query)

        if r_intent == "news":
            reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="news", reply=reply)

        if r_intent == "content":
            reply = _deterministic_pick(ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="content", reply=reply)

        if r_intent == "end":
            reply = "ладно."
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="end", reply=reply)

        if r_intent == "chat":
            semantic_chat = True

    tl = user_text.lower()
    if ("веся" in tl or "веслава" in tl) and ("новост" in tl or "дайджест" in tl):
        reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
        add_assistant(chat_id, user_id, reply)
        return DialogDecision(intent="news", reply=reply)

    # 0) Legacy persona rules are fallback only.
    # If semantic_route already classified this as chat, persona regex must not
    # upgrade it back into news/content.
    if semantic_chat:
        ir = None
    else:
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
        if not _explicit_action_request(user_text, "content"):
            # Если persona ошибочно распознала обычную фразу как запуск контента —
            # не запускаем подборку, а продолжаем как обычный chat.
            pass
        else:
            reply = _deterministic_pick(ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")
            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="content", reply=reply)

    if ir and ir.addressed and ir.intent == "unclear":
        # Непонятная, но адресованная Весе реплика — это обычный разговор,
        # а не выбор "контент / новости".
        try:
            client = OpenAI()
            resp = client.responses.create(
                model=DIALOG_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": (
                            getattr(persona, "_SYSTEM_PROMPT", "").strip()
                            + "\n\n"
                            "Это обычный разговор или реакция.\n"
                            "Никогда не пересказывай инструкцию пользователю. "
                            "Не пиши фразы вроде 'похоже, обычный вопрос', 'отвечай коротко', 'без лишней драмы'. "
                            "Это служебные правила, их нельзя выводить в ответ. "
                            "Пользователь НЕ просит контент и НЕ просит новости.\n"
                            "Нельзя уводить в контент или запускать подборки.\n"
                            "Отвечай как на обычный вопрос или реакцию.\n"
                            "Если пользователь прислал медиа — реагируй на него напрямую.\n"
                            "Сразу дай ответ, без вступлений.\n"
                            + _irritation_instruction(chat_id, user_id)
                        ),
                    },
                    *get_history(chat_id, user_id),
                ],
            )

            reply = _sanitize_reply(_extract_text(resp))
            reply = persona.postprocess_text(reply, user_text)
            reply = _dequestionize(reply)

            if not reply:
                reply = "Не цитирую дословно. Но суть передать могу."

            add_assistant(chat_id, user_id, reply)
            return DialogDecision(intent="chat", reply=reply)

        except Exception as e:
            print(f"[chatgpt_dialog] unclear chat EXC: {type(e).__name__}: {e}", flush=True)
            reply = "Не вышло ответить нормально. Великолепно."
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
        # Обычная беседа/творческий запрос: не отвечаем заготовками persona.answer_chat.
        # Пускаем ниже в free-chat LLM.
        pass

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
        if intent not in {"chat", "news", "content", "web_search", "end"}:
            intent = "chat"

        if semantic_chat and intent != "chat":
            intent = "chat"

        # LLM-router не имеет права сам запускать контент/новости из обычной беседы.
        # Контент/новости запускаются только по явным словам-триггерам.
        if intent in {"news", "content"} and not _explicit_action_request(user_text, intent):
            intent = "chat"
        reply = _sanitize_reply(str(data.get("reply", "")))
        reply = _dequestionize(reply)

        # жёстко убираем контент-ответы, если пользователь не просил
        if intent == "chat":
            if any(x in reply.lower() for x in [
                "жги",
                "контент",
                "подборку",
                "мемы",
                "видосы",
                "пошла искать",
                "пошла искать",
                "собираю",
                "ища уже",
                "ищу уже",
            ]):
                reply = ""
        
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
        print(f"[chatgpt_dialog] decide EXC: {type(e).__name__}: {e}", flush=True)
        reply = "мозг не завёлся. смотри лог [chatgpt_dialog]."
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

        user_task = (t or "").strip()
        asks_about_image = bool(re.search(
            r"\b(когда|почему|зачем|что думаешь|как думаешь|прокомментируй|разбери|поясни|объясни|это правда|ну и|и что|когда уже)\b",
            tl,
        ))

        if asks_about_image and not any(x in tl for x in ("шутк", "мем", "смешн", "юмор")):
            prompt = (
                "Пользователь прислал изображение/скрин и задал вопрос по нему.\n"
                f"Вопрос пользователя: {user_task}\n\n"
                "Сначала прочитай видимый текст на изображении. "
                "Пойми, о чём там речь. "
                "Ответь именно на вопрос пользователя, а не просто описывай картинку.\n"
                "Если пользователь спрашивает 'когда уже?' — это ироничный вопрос, не требующий точной даты. "
                "Можно ответить оценочно, саркастично, по смыслу изображения.\n"
                "Не говори 'не могу определить', если на изображении есть читаемый текст. "
                "Не делай нейтральную справку. "
                "Отвечай от лица Веси: коротко, холодно, с лёгкой иронией.\n"
                "Формат: 1–3 короткие фразы. По-русски. Без вопросов пользователю."
            )

        elif any(x in tl for x in ("шутк", "мем", "смешн", "юмор", "оцени", "оценить")):
            personal_to_vesya = bool(re.search(
                r"\b(ты|тебя|тебе|твой|твоя|твое|твоё|твои|сама|насколько ты|про тебя)\b",
                tl,
            ))

            if personal_to_vesya:
                prompt = (
                    "Пользователь прислал мем/шутку и задал личный вопрос Весе.\n"
                    f"Текст запроса пользователя: {user_task}\n\n"
                    "Главная задача — ответить на личный вопрос от лица Веси. "
                    "Картинка нужна как контекст шутки.\n"
                    "НЕ делай школьный разбор шутки.\n"
                    "НЕ начинай с 'шутка основана', 'смысл в том', 'удачность шутки'.\n"
                    "Ответь от первого лица как Веся.\n"
                    "Формат: 1–3 короткие фразы.\n"
                    "Стиль: сухо, умно, с лёгкой иронией.\n"
                    "По-русски. Без вопросов пользователю."
                )
            else:
                prompt = (
                    "Пользователь прислал мем/шутку и просит реакцию.\n"
                    f"Текст запроса пользователя: {user_task}\n\n"
                    "НЕ делай школьный разбор шутки.\n"
                    "НЕ объясняй длинно стереотипы.\n"
                    "НЕ начинай с фраз 'шутка основана', 'смысл в том', 'удачность шутки'.\n"
                    "Ответь от первого лица как Веся.\n"
                    "Сначала личная реакция Веси, потом максимум одно короткое пояснение.\n"
                    "Формат: 1–3 короткие фразы.\n"
                    "Стиль: сухо, умно, с лёгкой иронией.\n"
                    "По-русски. Без вопросов пользователю."
                )
        else:
            prompt = (
                "Определи, что изображено на фото.\n"
                f"Текст запроса пользователя: {user_task}\n\n"
                "Учитывай текст запроса пользователя как инструкцию к анализу изображения.\n"
                "Если пользователь просит оценить, сравнить, объяснить или прокомментировать — сделай именно это.\n"
                "Если это объект искусства, архитектуры, скульптуры, дизайна, декоративно-прикладного искусства, "
                "музейный экспонат, исторический объект, культурный артефакт или иная узнаваемая вещь — "
                "попробуй определить его как можно точнее.\n"
                "Если уверен — дай краткий экспертный ответ в формате: название объекта + 1 короткий факт или пояснение.\n"
                "Если точная идентификация неуверенная — прямо напиши 'не уверена' и дай 1–2 наиболее вероятных варианта.\n"
                "Если это не культурный объект и не узнаваемый артефакт, просто кратко скажи, что это.\n"
                "Без воды, без вопросов пользователю, по-русски."
            )
        
        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        getattr(persona, "_SYSTEM_PROMPT", "").strip()
                        + "\n\n"
                        "Ты отвечаешь на изображение от лица Веси, а не как безличный анализатор. "
                        "Если пользователь просит оценить шутку, мем или подпись — реагируй лично, в своём стиле. "
                        "Не пиши школьный разбор. Не объясняй длинно стереотипы, если можно ответить живо. "
                        "Сначала дай реакцию Веси, потом максимум одно короткое пояснение. "
                        "Если шутка обращена к тебе или содержит вопрос к тебе — отвечай как будто вопрос задан лично тебе. "
                        "Коротко, по-русски, без вопросов пользователю."
                    ),
                },
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

def comment_text_object(user_text: str, object_text: str) -> Optional[DialogDecision]:
    """
    Comment on forwarded/replied text in Vesya style.
    Does NOT route to news/content.
    """
    try:
        u = (user_text or "").strip()
        obj = (object_text or "").strip()

        if not obj:
            return DialogDecision(intent="chat", reply="Комментировать нечего. Пустота тоже жанр, но скучный.")

        if not _has_key():
            return DialogDecision(intent="chat", reply="Текст вижу, но мозг сейчас не подключен. Очень удобно.")

        client = OpenAI()

        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        getattr(persona, "_SYSTEM_PROMPT", "").strip()
                        + "\n\n"
                        "Ты — Веся. Пользователь дал тебе текст/пост/новость/пересланное сообщение и просит мнение. "
                        "НЕ запускай новости. НЕ запускай контент. НЕ предлагай подборки. "
                        "Твоя задача — прочитать смысл и дать короткий едкий комментарий в своём стиле. "
                        "Стиль: холодно, умно, саркастично, без блогерства и без канцелярита. "
                        "Формат: 1–2 короткие фразы. Без вопросов пользователю."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Вопрос пользователя: {u}\n\n"
                        f"Текст/объект для комментария:\n{obj[:6000]}"
                    ),
                },
            ],
        )

        reply = _sanitize_reply(_extract_text(resp))
        reply = persona.postprocess_text(reply, u)
        reply = _dequestionize(reply)

        return DialogDecision(
            intent="chat",
            reply=reply or "Прочитала. Пафоса больше, чем смысла."
        )

    except Exception as e:
        _dbg(f"comment_text_object EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="прочитать-то прочитала, а ответить нормально не вышло.")
    
def analyze_document_text(user_text: str, filename: str, doc_text: str) -> DialogDecision:
    """
    Analyze extracted document text.
    Extraction is done in main.py; this function only analyzes text.
    """
    try:
        u = (user_text or "").strip()
        fn = (filename or "document").strip()
        txt = (doc_text or "").strip()

        if not txt:
            return DialogDecision(intent="chat", reply="В документе не вижу текста. Либо скан, либо пустота под видом смысла.")

        if not _has_key():
            return DialogDecision(intent="chat", reply="Текст вытащила, но мозг сейчас не подключен.")

        client = OpenAI()

        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        getattr(persona, "_SYSTEM_PROMPT", "").strip()
                        + "\n\n"
                        "Ты анализируешь документ, который пользователь прислал в Telegram.\n"
                        "Опирайся только на текст документа. Не придумывай факты.\n"
                        "Если пользователь просит кратко — дай кратко.\n"
                        "Если просит проверить ошибки/риски — перечисли конкретные проблемы.\n"
                        "Если явной задачи нет — дай: 1) суть, 2) важные пункты, 3) риски/сомнительные места.\n"
                        "Стиль: по делу, сухо, без хамства к пользователю.\n"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Файл: {fn}\n"
                        f"Запрос пользователя: {u or 'проанализируй документ'}\n\n"
                        f"Текст документа:\n{txt[:30000]}"
                    ),
                },
            ],
        )

        reply = _sanitize_reply(_extract_text(resp))
        reply = persona.postprocess_text(reply, u)
        reply = _dequestionize(reply)

        return DialogDecision(
            intent="chat",
            reply=reply or "Прочитала документ. Смысла меньше, чем ожидалось."
        )

    except Exception as e:
        _dbg(f"analyze_document_text EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply=f"документ не разобрала: {type(e).__name__}")

def continue_topic_discussion(user_text: str, topic: dict) -> Optional[DialogDecision]:
    """
    Continue discussion of previously shared text/photo/video topic.
    Does NOT route to news/content.
    """
    try:
        u = (user_text or "").strip()
        topic = topic or {}

        if not topic:
            return DialogDecision(intent="chat", reply="Тему потеряла. Очень человечно, кстати.")

        if not _has_key():
            return DialogDecision(intent="chat", reply="Тему помню, но мозг сейчас не подключен. Красиво живём.")

        client = OpenAI()
        topic_text = json.dumps(topic, ensure_ascii=False)[:7000]

        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        getattr(persona, "_SYSTEM_PROMPT", "").strip()
                        + "\n\n"
                        "Ты — Веся. Пользователь продолжает обсуждать ранее присланный объект: текст, фото, мем или видео. "
                        "НЕ запускай новости. НЕ запускай контент. НЕ предлагай подборки. "
                        "Отвечай по сохранённой теме. "
                        "Стиль: коротко, холодно, саркастично, по делу. "
                        "Если спрашивают про музыку — используй music_track из темы. "
                        "Если music_track пустой — честно скажи, что трек не распознала. "
                        "Формат: 1–2 короткие фразы. Без вопросов пользователю."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Текущий вопрос пользователя: {u}\n\n"
                        f"Сохранённая тема:\n{topic_text}"
                    ),
                },
            ],
        )

        reply = _sanitize_reply(_extract_text(resp))
        reply = persona.postprocess_text(reply, u)
        reply = _dequestionize(reply)

        return DialogDecision(
            intent="chat",
            reply=reply or "Помню. Смысл там был примерно на уровне декоративного шума."
        )

    except Exception as e:
        _dbg(f"continue_topic_discussion EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="тему помню, но ответить нормально не вышло.")

def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    try:
        if not audio_bytes or not _has_key():
            return ""

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as f:
            f.write(audio_bytes)
            f.flush()

            client = OpenAI()
            r = client.audio.transcriptions.create(
                model=os.getenv("V_TRANSCRIBE_MODEL", "whisper-1"),
                file=open(f.name, "rb"),
            )

        return (getattr(r, "text", "") or "").strip()

    except Exception as e:
        _dbg(f"transcribe EXC: {type(e).__name__}: {e}")
        return ""

def recognize_music_audd(audio_bytes: bytes) -> str:
    """
    Recognize music track using AudD.io.
    Returns short human-readable track info or empty string.
    """
    try:
        token = (os.getenv("AUDD_API_TOKEN") or "").strip()
        if not token or not audio_bytes:
            return ""

        import tempfile
        import requests

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as f:
            f.write(audio_bytes)
            f.flush()

            with open(f.name, "rb") as af:
                r = requests.post(
                    "https://api.audd.io/",
                    data={
                        "api_token": token,
                        "return": "apple_music,spotify",
                    },
                    files={
                        "file": ("audio.mp3", af, "audio/mpeg"),
                    },
                    timeout=25,
                )

        data = r.json()
        if data.get("status") != "success":
            return ""

        result = data.get("result")
        if not isinstance(result, dict):
            return ""

        title = (result.get("title") or "").strip()
        artist = (result.get("artist") or "").strip()
        album = (result.get("album") or "").strip()

        if title and artist:
            if album:
                return f"{artist} — {title} ({album})"
            return f"{artist} — {title}"

        if title:
            return title

        return ""

    except Exception as e:
        _dbg(f"audd recognize EXC: {type(e).__name__}: {e}")
        return ""

def describe_video_frames(
    text: str,
    frame_bytes_list: List[bytes],
    audio_bytes: bytes | None = None,
) -> Optional[DialogDecision]:
    try:
        frames = [b for b in (frame_bytes_list or []) if b]
        audio = audio_bytes or b""
        transcript = transcribe_audio_bytes(audio)
        music_track = recognize_music_audd(audio)

        if not frames and not transcript:
            return DialogDecision(intent="chat", reply="из видео ничего не вытащила. Пустой фокус.")

        if not _has_key():
            return DialogDecision(intent="chat", reply="видео вижу, но мозг для анализа сейчас не подключен.")

        client = OpenAI()
        user_task = (text or "").strip()
        user_task_l = user_task.lower()
        asks_music = any(x in user_task_l for x in (
            "музык", "песня", "трек", "что играет", "кто поет", "кто поёт",
            "исполнитель", "название", "shazam", "шазам"
        ))

        asks_personal_opinion = any(x in user_task_l for x in (
            "как тебе", "тебе как", "нравится", "что думаешь", "что скажешь",
            "оцени", "твое мнение", "твоё мнение", "как оно"
        ))

        content = [{
            "type": "input_text",
            "text": (
                "Пользователь прислал видео и спрашивает мнение Веси.\n"
                f"Запрос пользователя: {user_task}\n\n"
                f"Пользователь просит личную оценку, а не описание: {asks_personal_opinion}\n"
                "Если это личная оценка — НЕ начинай с описания сцены. "
                "Сразу отвечай как Веся: нравится/не нравится/смешно/скучно/пошло/нормально, с коротким сарказмом.\n\n"
                f"Транскрибация речи, если была речь:\n{transcript or '[речи не распознано]'}\n\n"
                f"Распознанная музыка:\n{(music_track or '[трек не распознан]') if asks_music else '[не упоминать музыку]'}\n\n"
                "Ниже несколько кадров из видео по порядку.\n"
                "Кадры используй как материал для оценки, а не как повод пересказывать видео.\n"
                "Не избегай описания людей, внешности и позы, если это ключевой элемент сцены.\n"
                "Не сглаживай очевидный сексуальный, комичный или конфликтный контекст.\n"
                "Если речь не распознана — не выдумывай речь.\n"
                "Музыку, песню, трек и исполнителя упоминай только если пользователь прямо спросил про музыку. "
                "Если не спросил — вообще не пиши про музыку.\n"
                "Ответь от лица Веси: холодно, саркастично, коротко, не как блогер и не как обзорщик видео.\n"
                "Не пиши пресно: избегай слов вроде 'завораживающий', 'гармония', 'атмосфера', если можно сказать острее.\n"
                "Если на видео явно есть тело, поза, сексуальный акцент или демонстративность — не делай вид, что это только пейзаж.\n"
                "Запрещены блогерские слова: 'эффектная', 'атмосфера', 'наслаждаются', 'ярко и открыто', 'создаёт настроение'.\n"
                "Формат: 1–2 короткие фразы. По-русски. Без вопросов пользователю."
            ),
        }]

        for b in frames[:5]:
            b64 = base64.b64encode(b).decode("utf-8")
            content.append({
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{b64}",
            })

        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        getattr(persona, "_SYSTEM_PROMPT", "").strip()
                        + "\n\n"
                        "Ты — Веся. Холодная, уверенная, с сарказмом. "
                        "Никогда не говоришь как блогер или обзорщик. "
                        "Запрещено: описывать 'красиво', 'эффектно', 'создаёт атмосферу', 'выглядит', 'позирует'. "
                        "Говори как человек, которому это всё уже надоело. "
                        "Если в кадре тело или демонстрация — называй прямо, без сглаживания. "
                        "Сначала личная реакция, потом короткое пояснение. "
                        "Формат: 1–2 фразы. Без воды. Без вопросов."
                    ),
                },
                {"role": "user", "content": content},
            ],
        )

        reply = _sanitize_reply(_extract_text(resp))
        reply = persona.postprocess_text(reply, user_task)
        reply = _dequestionize(reply)

        if asks_personal_opinion:
            descriptive_starts = (
                "море и", "море,", "девушка", "девушки", "на видео",
                "в ролике", "видно", "очевидно", "кажется,"
            )
            if reply.lower().startswith(descriptive_starts):
                reply = "Не мой жанр. Слишком много старания ради слишком понятного эффекта."

        bad_video_words = [
            "эффектная", "позирует", "акцент", "атмосфер",
            "внешности", "выглядит", "создавая", "добавляет",
            "откровенно", "блогер", "эстетично",
        ]

        if any(w in reply.lower() for w in bad_video_words):
            reply = "Пенная витрина тел. Сюжет утонул первым."

        if not asks_music:
            reply = re.sub(
                r"(?i)\s*(музыка|трек|песня|исполнитель)[^.!\n]*[.!\n]?",
                " ",
                reply,
            ).strip()

        return DialogDecision(
            intent="chat",
            reply=reply or "Видео посмотрела. Понтов больше, чем сюжета."
        )

    except Exception as e:
        _dbg(f"video discuss EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="видео разобрать не вышло.")

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