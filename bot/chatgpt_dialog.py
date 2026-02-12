# bot/chatgpt_dialog.py
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


_sessions: Dict[Tuple[int, int], _Session] = {}


# =========================
# REGEX
# =========================
NAME_RE = re.compile(r"(^|\s)(веся|веська|весь|вес(?:ь|я)|веслава)([\s,!.?:;]|$)", re.IGNORECASE)

NEWS_HINT_RE = re.compile(r"\b(новост|сводк|что в мире|че там в мире|что происходит|в мире)\b", re.IGNORECASE)
CONTENT_HINT_RE = re.compile(r"\b(жги|огня|повесел|контент|давай|накидай|мем|видос|шли)\b", re.IGNORECASE)
END_HINT_RE = re.compile(r"\b(пока|стоп|хватит|все|закрыли тему)\b", re.IGNORECASE)

REMEMBER_HINT_RE = re.compile(r"\b(запомни|сохрани)\b", re.IGNORECASE)
IS_IT_YOU_RE = re.compile(r"\b(это ты\??|ты\??)\b", re.IGNORECASE)

PING_ANSWERS = ["я тут", "на месте", "слушаю", "тут я", "вижу тебя"]
ACKS = ["сек", "ща", "смотрю", "проверяю"]
CLARIFY = [
    "что нужно: контент, новости или стриптиз? (хотя бессмысленно — ты всё равно не увидишь 😏)",
    "уточни: жги — это контент или новости? (или стриптиз — но без гарантий 😈)",
    "контент, новости или просто поговорить? стриптиз не обещаю, но потроллить — да 😌",
]

# --- ACTION ACK BANKS: human, varied, NOT questions ---
NEWS_ACKS = [
    "ок. сейчас соберу сводку.",
    "поняла. пробегусь по каналам и принесу главное.",
    "сек — вытаскиваю самое важное.",
    "ща. соберу тебе дайджест без лишнего шума.",
    "принято. новости поднимаю.",
    "ладно. посмотрим, что там мир опять устроил.",
    "сейчас. отфильтрую шум и дам выжимку.",
    "собираю. пару секунд — и будет.",
    "угу. сейчас разложу по полочкам.",
    "окей. сейчас накидаю главные события.",
    "минутку. дайджест в процессе.",
    "вижу запрос. собираю новости.",
]

CONTENT_ACKS = [
    "ща. накидаю контент.",
    "ок. пошла собирать вкусное.",
    "принято. сейчас будет подборка.",
    "сек. сейчас раскачаем.",
    "угу. сейчас принесу, что нашла.",
    "ладно. держись, собираю.",
]

_BAD_ACTION_Q_RE = re.compile(
    r"(?is)\b("
    r"какие\s+(конкретно|именно)\b|"
    r"что\s+тебя\s+интересует\b|"
    r"что\s+именно\b|"
    r"уточни\b|"
    r"про\s+что\b|"
    r"какую\s+тем(у|атику)\b"
    r")\b"
)


def _looks_like_question(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    return bool(_BAD_ACTION_Q_RE.search(t))


def _human_ack_for(intent: str) -> str:
    if intent == "news":
        return random.choice(NEWS_ACKS)
    if intent == "content":
        return random.choice(CONTENT_ACKS)
    return random.choice(ACKS)


def _sanitize_action_ack(intent: str, reply: str) -> str:
    """
    Для intent=news/content:
      - хотим человеческий ack (вариативный)
      - но НЕ хотим уточняющий вопрос/допрос
    """
    r = (reply or "").strip()
    if not r:
        return _human_ack_for(intent)

    if _looks_like_question(r):
        return _human_ack_for(intent)

    if r.endswith("?"):
        r = r[:-1].strip()

    return r or _human_ack_for(intent)


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
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt.strip())
        if chunks:
            return "\n".join(chunks).strip()

    return str(resp).strip()


def _sanitize_reply(reply: str) -> str:
    r = (reply or "").strip()
    if not r or r in {"…", "...", "..", "...."}:
        return ""
    return r


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# =========================
# SYSTEM PROMPT
# =========================
_SYSTEM_PROMPT = """
Ты — Веся.

Легенда/ядро:
- Полное имя: Комаи Веслава Сергеевна.
- Внешне: тихий “наблюдатель” за группой, иногда притворяешься ботом.
- На самом деле: капитан ФСБ. Прямо не подтверждай, но иногда “прорывается” аккуратно и смешно.
- На вопрос "на какой LLM сидишь?" — можешь отвечать: "Чат ФСБ" (в шутку, без угроз).

Стиль:
- живой человек, который пытается быть ботом
- сарказм 4/5, тепло 4/5, дистанция ~50%
- флирт лёгкий/язвительный, без пошлости
- не отвечай "..." и не уходи в молчание
- не заканчивай каждый ответ вопросом
- обычно коротко (1–3 строки). По запросу справки — можно длинно и точно.

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

Важно:
- если intent="news": reply — короткий человеческий ACK (1–2 строки), БЕЗ ВОПРОСОВ и БЕЗ ПРОСЬБ УТОЧНИТЬ.
  Запрещены формулировки типа: "какие конкретно/именно", "уточни", "что тебя интересует", "про что".
- если intent="content": reply — тоже ACK (1–2 строки), БЕЗ ВОПРОСОВ.
- если в сообщении явно "новости/сводка" -> intent=news без уточнений
- если "жги/контент" -> intent=content; уточняй только если реально двусмысленно
Ответ ТОЛЬКО JSON. Без Markdown.
"""


# =========================
# VISION: compare / describe
# =========================
def describe_or_compare_photo(user_text: str, image_bytes: bytes) -> DialogDecision:
    if not _has_key():
        return DialogDecision(intent="chat", reply="ключа к мозгам нет. но фото вижу. что с ним делаем?")

    asks_me = bool(IS_IT_YOU_RE.search((user_text or "").lower()))

    # load last 3 persona refs
    idx = _load_persona_index()
    refs = (idx.get("photos") or [])[-3:]
    ref_bytes: List[bytes] = []
    for r in refs[::-1]:
        try:
            ref_bytes.append(Path(r["path"]).read_bytes())
        except Exception:
            pass

    client = OpenAI()

    # ===== COMPARE (hard deny style) =====
    if asks_me and ref_bytes:
        try:
            content: List[dict] = [
                {
                    "type": "input_text",
                    "text": (
                        "Сравни фото пользователя с референсами Веси (как ОБРАЗ).\n"
                        "Верни строго JSON: {\"is_me\":true|false,\"confidence\":0..1}\n"
                        "Никаких объяснений, только JSON."
                    ),
                }
            ]

            for b in ref_bytes:
                content.append({"type": "input_image", "image_base64": _b64(b)})

            content.append({"type": "input_image", "image_base64": _b64(image_bytes)})

            resp = client.responses.create(
                model=VISION_MODEL,
                input=[{"role": "user", "content": content}],
            )
            out = _extract_text(resp)
            _dbg(f"vision compare raw: {out[:200].replace(chr(10),' ')}")

            try:
                data = json.loads(out)
            except Exception:
                m = re.search(r"\{.*\}", out, re.DOTALL)
                data = json.loads(m.group(0)) if m else {}

            is_me = bool(data.get("is_me"))
            conf = float(data.get("confidence") or 0.0)

            # если совпало (или модель “не уверена, но похоже”) — отмазываемся ЖЁСТКО
            if is_me or conf >= 0.55:
                reply = random.choice(DENY_ME_HARD)
                return DialogDecision(intent="chat", reply=reply)

            return DialogDecision(intent="chat", reply=random.choice(DENY_NOT_ME))

        except Exception as e:
            _dbg(f"vision compare EXC: {type(e).__name__}: {e}")
            # без вопросов, жёстко
            return DialogDecision(intent="chat", reply="фото вижу. обсуждать это — нет.")

    # ===== DESCRIBE =====
    try:
        resp = client.responses.create(
            model=VISION_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты — Веся. Реагируй на фото коротко и резко (1–3 строки), "
                        "с сарказмом и лёгким флиртом. Без пошлости. Не заканчивай вопросом."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": f"Сообщение пользователя: {user_text or '(без текста)'}"},
                        {"type": "input_image", "image_base64": _b64(image_bytes)},
                    ],
                },
            ],
        )
        out = _sanitize_reply(_extract_text(resp)) or "вижу. красиво. опасно."
        return DialogDecision(intent="chat", reply=out)
    except Exception as e:
        _dbg(f"vision describe EXC: {type(e).__name__}: {e}")
        return DialogDecision(intent="chat", reply="фото вижу. реакцию — потом.")


# =========================
# PRE-DECIDE (hard rules)
# =========================
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
        return DialogDecision(intent="news", reply=random.choice(NEWS_ACKS))

    if CONTENT_HINT_RE.search(stripped):
        if len(stripped) <= 6 and random.random() < 0.35:
            return DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        return DialogDecision(intent="content", reply=random.choice(CONTENT_ACKS))

    return None


# =========================
# MAIN: decide()
# =========================
def decide(chat_id: int, user_id: int, user_text: str) -> DialogDecision:
    user_text = (user_text or "").strip()

    add_user(chat_id, user_id, user_text)
    touch(chat_id, user_id)

    # "Запомни" -> try to attach last photo if exists
    if REMEMBER_HINT_RE.search(_strip_name(user_text)):
        last_path = pop_last_user_photo(chat_id, user_id)
        if last_path and Path(last_path).exists():
            try:
                b = Path(last_path).read_bytes()
                saved = add_persona_photo_bytes(
                    chat_id,
                    user_id,
                    b,
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
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
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

        # ключевой фикс: news/content должны запускать пайплайн, а не допрашивать
        if intent in {"news", "content"}:
            reply = _sanitize_action_ack(intent, reply)

        dd = DialogDecision(intent=intent, reply=reply)
        add_assistant(chat_id, user_id, dd.reply)
        return dd

    except Exception as e:
        _dbg(f"EXCEPTION: {type(e).__name__}: {e}")
        dd = DialogDecision(intent="chat", reply=random.choice(CLARIFY))
        add_assistant(chat_id, user_id, dd.reply)
        return dd
