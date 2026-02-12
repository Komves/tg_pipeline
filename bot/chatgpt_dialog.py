from __future__ import annotations

import json
import os
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

DIALOG_TTL_SEC = int(os.getenv("DIALOG_TTL_SEC", "3600"))
DIALOG_MAX_TURNS = int(os.getenv("DIALOG_MAX_TURNS", "30"))

DIALOG_MODEL = os.getenv("DIALOG_MODEL", "gpt-4o-mini")
DEBUG_DIALOG = os.getenv("DEBUG_DIALOG", "0") == "1"

PERSONA_INDEX = DATA_DIR / "persona_index.json"

# =========================
# REGEX + STATIC TEXT
# =========================
NAME_RE = re.compile(r"^\s*(веся|вес|vesya)\s*[:,]?\s*", re.I)
REMEMBER_HINT_RE = re.compile(r"\b(запомни|remember)\b", re.I)

INTENT_SET = {"chat", "news", "content", "end"}

# Variative clarification prompts (deterministic pick + no repeat twice in a row)
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

DENY_ME_HARD = [
    "Инфа по этому фото закрыта. Спроси что-нибудь полезное 😌",
    "Я себя не идентифицирую. И тебя тоже, если честно 😏",
    "Не-а. Никаких «кто на фото». Давай лучше по делу.",
]

# =========================
# HELPERS
# =========================
def _now() -> float:
    return time.time()


def _dbg(msg: str) -> None:
    if DEBUG_DIALOG:
        print(f"[chatgpt_dialog] {msg}")


def _has_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


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


def _sanitize_reply(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"```(?:json)?", "", t, flags=re.I).strip()
    t = t.replace("```", "").strip()
    if len(t) > 600:
        t = t[:600].rstrip() + "…"
    return t


def _extract_text(resp: Any) -> str:
    # openai.responses style
    try:
        out: List[str] = []
        for item in resp.output:
            if getattr(item, "type", "") == "message":
                for c in item.content:
                    if getattr(c, "type", "") == "output_text":
                        out.append(getattr(c, "text", ""))
        return "".join(out).strip()
    except Exception:
        pass

    # chat.completions fallback
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _normalize_intent(intent: str) -> str:
    x = (intent or "").strip().lower()
    if x in INTENT_SET:
        return x
    if "news" in x or "новост" in x:
        return "news"
    if "content" in x or "контент" in x or "пост" in x:
        return "content"
    if "end" in x or "stop" in x or "пока" in x:
        return "end"
    return "chat"


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
        "не умею",
        "не могу",
        "нет доступа",
        "не доступно",
        "в этом проекте",
        "в коде",
        "в репозитории",
    ]
    return any(b in rl for b in bad)


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


_ACTION_ACKS_NEWS = [
    "Сек, соберу новости.",
    "Сейчас посмотрю новости.",
    "Минутку — подбираю главное.",
    "Ок, уже ищу свежие новости.",
    "Понял, собираю дайджест.",
    "Сейчас проверю, что нового.",
]

_ACTION_ACKS_CONTENT = [
    "Угу. Сейчас принесу, что нашла.",
    "Ок, сейчас соберу контент.",
    "Минутку — подбираю идеи.",
    "Сек, подготовлю подборку.",
    "Понял, уже собираю материалы.",
]


# =========================
# DIALOG STATE (IN-MEM)
# =========================
@dataclass
class _Session:
    expires_at: float
    history: Deque[Dict[str, str]]
    active: bool = True
    last_clarify_idx: Optional[int] = None


_sessions: Dict[Tuple[int, int], _Session] = {}


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


# =========================
# PERSONA PHOTO STORAGE API
# =========================
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
    folder = DATA_DIR / "persona_photos"
    folder.mkdir(parents=True, exist_ok=True)
    ts = int(_now())
    fn = f"{chat_id}_{user_id}_{ts}.{ext}"
    path = folder / fn
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


def pop_last_user_photo(chat_id: int, user_id: int) -> Optional[str]:
    # kept for compatibility if main.py imports it elsewhere
    return None


def describe_or_compare_photo(text: str, img_bytes: bytes):
    # kept for compatibility if main.py imports it elsewhere
    return None


# =========================
# DECISION STRUCT
# =========================
@dataclass
class DialogDecision:
    intent: str
    reply: str


# =========================
# PRE-DECIDE (FAST RULES)
# =========================
def _pre_decide(user_text: str) -> Optional[DialogDecision]:
    t = (user_text or "").strip()
    tl = t.lower()

    if not t:
        return DialogDecision(intent="chat", reply="")

    if _looks_like_ping(t):
        return DialogDecision(intent="chat", reply="да?")

    if any(x in tl for x in ["пока", "стоп", "хватит", "выключись", "конец", "до связи"]):
        return DialogDecision(intent="end", reply="Ок.")

    if any(x in tl for x in ["новости", "дайджест", "что нового", "новост"]):
        return DialogDecision(intent="news", reply="Сек, соберу новости.")

    # "Жги / дай огня / прогон / ингест" => content intent (run_all -> ingest)
    if any(
        x in tl
        for x in [
            "жги",
            "дай огня",
            "огонь",
            "врубай",
            "погнали",
            "поехали",
            "прогон",
            "ингест",
            "ingest",
            "дай контент",
            "контент",
            "пост",
            "идеи для поста",
            "сценарий",
            "шортс",
            "tiktok",
        ]
    ):
        return DialogDecision(intent="content", reply="Угу. Сейчас принесу, что нашла.")

    return None


# =========================
# SYSTEM PROMPT
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


# =========================
# MAIN API
# =========================
def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    user_text = (user_text or "").strip()

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    # "Запомни" -> try to attach last photo if exists
    if REMEMBER_HINT_RE.search(_strip_name(user_text)):
        last_path = get_last_persona_photo_path(chat_id, user_id)
        if last_path:
            try:
                saved = save_persona_photo(
                    chat_id,
                    user_id,
                    Path(last_path).read_bytes(),
                    ext=Path(last_path).suffix.lstrip(".") or "jpg",
                    note="remember_text",
                )
                dd = DialogDecision(intent="chat", reply=f"принято. закрепила у себя в досье как образ: {saved}")
                add_assistant(chat_id, user_id, dd.reply)
                return dd
            except Exception as e:
                _dbg(f"remember EXC: {type(e).__name__}: {e}")
                dd = DialogDecision(intent="chat", reply="хотела запомнить, но уронила фото. кинь ещё раз.")
                add_assistant(chat_id, user_id, dd.reply)
                return dd

        dd = DialogDecision(
            intent="chat",
            reply="Ок. Что именно запомнить: одну фразу текстом или фото? (если фото — просто пришли и повтори «Запомни»).",
        )
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    pre = _pre_decide(user_text)
    if pre is not None:
        add_assistant(chat_id, user_id, pre.reply)
        return pre

    if not _has_key():
        dd = DialogDecision(intent="chat", reply=_pick_clarify(chat_id, user_id, user_text))
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    hist = get_history(chat_id, user_id)
    client = OpenAI()

    try:
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[{"role": "system", "content": _SYSTEM_PROMPT}, *hist],
        )

        out = _extract_text(resp)
        _dbg(f"raw model out: {out[:200].replace(chr(10),' ')}")

        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r"\{.*\}", out, re.DOTALL)
            data = json.loads(m.group(0)) if m else None

        if not isinstance(data, dict):
            reply = _sanitize_reply(out) or _pick_clarify(chat_id, user_id, user_text)
            dd = DialogDecision(intent="chat", reply=reply)
            add_assistant(chat_id, user_id, dd.reply)
            return dd

        intent = _normalize_intent(str(data.get("intent", "")))
        reply = _sanitize_reply(str(data.get("reply", "")))

        # Guardrail: action intents must not ask clarification OR talk meta about code/pipelines
        if intent == "news" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply)):
            reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
        elif intent == "content" and (_looks_like_clarification(reply) or _looks_like_meta_pipeline(reply)):
            reply = _deterministic_pick(_ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")

        # Guardrail: for non-action intents, strip meta-pipeline talk too
        if intent not in {"news", "content"} and _looks_like_meta_pipeline(reply):
            reply = ""

        if not reply:
            if intent == "news":
                reply = _deterministic_pick(_ACTION_ACKS_NEWS, f"news:{chat_id}:{user_id}:{user_text}")
            elif intent == "content":
                reply = _deterministic_pick(_ACTION_ACKS_CONTENT, f"content:{chat_id}:{user_id}:{user_text}")
            else:
                reply = _pick_clarify(chat_id, user_id, user_text)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"decide EXC: {type(e).__name__}: {e}")
        dd = DialogDecision(intent="chat", reply=_pick_clarify(chat_id, user_id, user_text))
        add_assistant(chat_id, user_id, dd.reply)
        return dd
