# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations
import asyncio
import os
import time
import random
import json
import shutil
import uuid
import html
import base64
import clip_embedder
import re
from pathlib import Path
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
import io
from PIL import Image

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes

import c_youtube_fetcher
# deploy trigger
RECENT_MSG_IDS = {}
VOICE_TEXT_MSG_IDS = {}
GMAIL_LAST_MESSAGES = {}  # user_id -> list[dict]
GMAIL_POLL_INTERVAL_SEC = int(os.getenv("GMAIL_POLL_INTERVAL_SEC", "900"))
GMAIL_LAST_MESSAGES = {}  # user_id -> list[dict]# last image per (chat_id, user_id) to support "опиши фото" without reply
LAST_USER_IMAGE_ID = {}

PENDING_FORWARD_ACTION = {}  # legacy; kept for compatibility
PENDING_FORWARD_ACTION_TTL_SEC = 120

PENDING_MESSAGE_OBJECT_REQUEST = {}  # (chat_id, user_id) -> {"instruction": str, "ts": float}
PENDING_MESSAGE_OBJECT_TTL_SEC = 120

# =========================
# IMAGE REACTION LIMITER (moderate)
# =========================
IMG_REACT_LAST_TS = {}  # chat_id -> ts
IMG_REACT_COOLDOWN_SEC = int(os.getenv("V_IMG_REACT_COOLDOWN_SEC", "20"))
IMG_REACT_PROB = float(os.getenv("V_IMG_REACT_PROB", "0.45"))

FEEDBACK_PATH = "/data/feedback.tsv"
def _is_banned(item_id: str) -> bool:
    try:
        p = Path(FEEDBACK_PATH)
        if not p.exists():
            return False
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if "\tban\t" in line and item_id in line:
                    return True
    except Exception:
        pass
    return False

def _load_sent(path: Path) -> set[str]:
    """
    Load "sent" ids from disk.
    Stored as list (order preserved), returned as set for fast membership checks.
    """
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(x) for x in data if x)
    except Exception:
        pass
    return set()

def _save_sent(path: Path, sent: set[str], *, keep_last: int = 500) -> None:
    """
    Save with stable order:
    - read existing list
    - append new ids in deterministic order (sorted)
    - trim from the left (keep last N)
    """
    try:
        existing = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    existing = [str(x) for x in data if x]
            except Exception:
                existing = []

        exist_set = set(existing)

        # deterministic append (so we don't "forget" randomly)
        new_ids = sorted([x for x in sent if x and x not in exist_set])

        merged = existing + new_ids
        if len(merged) > keep_last:
            merged = merged[-keep_last:]

        path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

async def _download_tg_file_bytes(bot: Bot, file_id: str) -> bytes:
    """
    Скачать файл Telegram по file_id → bytes
    """
    from io import BytesIO

    tg_file = await bot.get_file(file_id)
    bio = BytesIO()
    await bot.download_file(tg_file.file_path, destination=bio)
    return bio.getvalue()

async def _gpt_meme_ok(abs_path: str, src: str = "") -> bool:
    try:
        p = Path(abs_path)
        if not p.exists():
            return True

        img_bytes = p.read_bytes()

        cap = ""
        mp = Path(str(p) + ".meta.json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                cap = (meta.get("caption") or "").strip()
                if not src:
                    src = (meta.get("src") or "").strip()
            except Exception:
                pass

        return bool(chatgpt_dialog.meme_should_send(img_bytes, caption=cap, src=src))
    except Exception:
        return False


TG_LOCK = asyncio.Lock()
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

import chatgpt_dialog
import news_digest
import memory as vesya_memory
from analytics_agent.gateway import handle_analytics_message, handle_analytics_photo, handle_analytics_callback, is_analytics_active
from vesya_tools.calendar.handler import handle_calendar_message
from vesya_tools.calendar.scheduler import calendar_loop
from vesya_tools.calendar.storage import CalendarStorage

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes


# =========================
# ENV / CONFIG
# =========================
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "45"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty (set Render env var BOT_TOKEN).")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CALENDAR_STORAGE = CalendarStorage(DATA_DIR / "vesya_calendar.sqlite3")

TOPIC_TTL_SEC = int(os.getenv("V_TOPIC_TTL_SEC", str(7 * 24 * 3600)))
TOPIC_PATH = DATA_DIR / "vesya_topics.json"

TRANSLATOR_MODES_PATH = DATA_DIR / "vesya_translator_modes.json"
SECRETARY_MODES_PATH = DATA_DIR / "vesya_secretary_modes.json"
BLOCKED_USERS_PATH = DATA_DIR / "vesya_blocked_users.json"

def _load_blocked_users() -> set[int]:
    try:
        if BLOCKED_USERS_PATH.exists():
            data = json.loads(BLOCKED_USERS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {int(x) for x in data}
    except Exception:
        pass
    return set()

def _save_blocked_users(users: set[int]) -> None:
    try:
        tmp = BLOCKED_USERS_PATH.with_suffix(BLOCKED_USERS_PATH.suffix + ".tmp")
        tmp.write_text(
            json.dumps(sorted(int(x) for x in users), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(BLOCKED_USERS_PATH)
    except Exception:
        pass

def _is_blocked_user(user_id: int) -> bool:
    return int(user_id or 0) in _load_blocked_users()


GROUP_MUTES_PATH = DATA_DIR / "vesya_group_mutes.json"


def _load_group_mutes() -> dict:
    try:
        if GROUP_MUTES_PATH.exists():
            data = json.loads(GROUP_MUTES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_group_mutes(data: dict) -> None:
    try:
        tmp = GROUP_MUTES_PATH.with_suffix(GROUP_MUTES_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GROUP_MUTES_PATH)
    except Exception:
        pass


def _group_mute_until(chat_id: int) -> float:
    data = _load_group_mutes()
    try:
        until = float(data.get(str(int(chat_id))) or 0)
    except Exception:
        until = 0

    if until and until <= time.time():
        data.pop(str(int(chat_id)), None)
        _save_group_mutes(data)
        return 0

    return until


def _is_group_muted(chat_id: int) -> bool:
    return _group_mute_until(chat_id) > time.time()


def _set_group_mute(chat_id: int, seconds: int) -> None:
    data = _load_group_mutes()
    data[str(int(chat_id))] = time.time() + max(60, int(seconds))
    _save_group_mutes(data)


def _clear_group_mute(chat_id: int) -> None:
    data = _load_group_mutes()
    data.pop(str(int(chat_id)), None)
    _save_group_mutes(data)


def _parse_group_mute_seconds(text: str) -> int | None:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" ?!.,:;")

    if not re.search(r"\b(не отвечай|молчи|замолчи|пауза)\b", t, flags=re.I):
        return None

    if re.search(r"\b(сутки|день)\b", t, flags=re.I):
        return 24 * 3600

    m = re.search(r"(\d{1,3})\s*(час|часа|часов|ч)\b", t, flags=re.I)
    if m:
        return int(m.group(1)) * 3600

    m = re.search(r"(\d{1,3})\s*(минут|минуты|мин|м)\b", t, flags=re.I)
    if m:
        return int(m.group(1)) * 60

    return None


def _is_group_unmute_command(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" ?!.,:;")

    return bool(re.search(
        r"\b(можешь отвечать|сними паузу|отмена паузы|вернись|включись|говори)\b",
        t,
        flags=re.I,
    ))

def _translator_key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"


def _load_translator_modes() -> dict:
    try:
        if TRANSLATOR_MODES_PATH.exists():
            data = json.loads(TRANSLATOR_MODES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_translator_modes(data: dict) -> None:
    try:
        tmp = TRANSLATOR_MODES_PATH.with_suffix(TRANSLATOR_MODES_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TRANSLATOR_MODES_PATH)
    except Exception:
        pass


def _get_translator_mode(chat_id: int, user_id: int) -> dict | None:
    data = _load_translator_modes()
    mode = data.get(_translator_key(chat_id, user_id))
    return mode if isinstance(mode, dict) and mode.get("enabled") else None


def _set_translator_mode(chat_id: int, user_id: int, lang_a: str, lang_b: str) -> None:
    data = _load_translator_modes()
    data[_translator_key(chat_id, user_id)] = {
        "enabled": True,
        "lang_a": (lang_a or "").strip(),
        "lang_b": (lang_b or "").strip(),
        "created_at": time.time(),
    }
    _save_translator_modes(data)


def _clear_translator_mode(chat_id: int, user_id: int) -> None:
    data = _load_translator_modes()
    data.pop(_translator_key(chat_id, user_id), None)
    _save_translator_modes(data)


def _secretary_key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"


def _load_secretary_modes() -> dict:
    try:
        if SECRETARY_MODES_PATH.exists():
            data = json.loads(SECRETARY_MODES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_secretary_modes(data: dict) -> None:
    try:
        tmp = SECRETARY_MODES_PATH.with_suffix(SECRETARY_MODES_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SECRETARY_MODES_PATH)
    except Exception:
        pass


def _is_secretary_mode_active(chat_id: int, user_id: int) -> bool:
    data = _load_secretary_modes()
    mode = data.get(_secretary_key(chat_id, user_id))
    return bool(isinstance(mode, dict) and mode.get("enabled"))


def _set_secretary_mode(chat_id: int, user_id: int) -> None:
    data = _load_secretary_modes()
    data[_secretary_key(chat_id, user_id)] = {
        "enabled": True,
        "created_at": time.time(),
    }
    _save_secretary_modes(data)


def _clear_secretary_mode(chat_id: int, user_id: int) -> None:
    data = _load_secretary_modes()
    data.pop(_secretary_key(chat_id, user_id), None)
    _save_secretary_modes(data)


def _parse_translator_on_command(text: str) -> tuple[str, str] | None:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" .,!?:;")

    m = re.search(
        r"^(?:включи|активируй|запусти)\s+(?:режим\s+)?переводчика\s+(?:с|из)\s+(.+?)\s+на\s+(.+?)(?:\s+и\s+обратно)?$",
        t,
        flags=re.I,
    )
    if not m:
        return None

    lang_a = (m.group(1) or "").strip()
    lang_b = (m.group(2) or "").strip()

    if not lang_a or not lang_b:
        return None

    return lang_a, lang_b


def _is_translator_off_command(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" .,!?:;")

    return bool(re.search(
        r"^(?:отключи|выключи|останови|заверши)\s+(?:режим\s+)?переводчика$",
        t,
        flags=re.I,
    ))


def _translate_bidirectional(text: str, lang_a: str, lang_b: str) -> str:
    src = (text or "").strip()
    if not src:
        return ""

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return ""

    try:
        from openai import OpenAI

        client = OpenAI()

        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты строгий онлайн-переводчик.\n"
                        "Никакой личности, комментариев, пояснений, приветствий или шуток.\n"
                        "Есть две стороны перевода:\n"
                        f"A: {lang_a}\n"
                        f"B: {lang_b}\n\n"
                        "Определи, ближе ли входной текст к языку A или к языку B.\n"
                        "Если текст на языке A — переведи на язык B.\n"
                        "Если текст на языке B — переведи на язык A.\n"
                        "Если язык смешанный, переведи на противоположный от преобладающего.\n"
                        "Верни только перевод. Без кавычек. Без пометок языка."
                    ),
                },
                {
                    "role": "user",
                    "content": src,
                },
            ],
        )

        return (getattr(resp, "output_text", "") or "").strip()

    except Exception as e:
        print(f"[translator] translate failed: {type(e).__name__}: {e}", flush=True)
        return ""

# === PRIVATE USERS REGISTRY ===
PRIVATE_USERS_PATH = DATA_DIR / "private_users.json"

def _load_private_users() -> set[int]:
    try:
        if PRIVATE_USERS_PATH.exists():
            return set(json.loads(PRIVATE_USERS_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()

def _save_private_users(users: set[int]) -> None:
    try:
        PRIVATE_USERS_PATH.write_text(
            json.dumps(list(users), ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass
def _format_morning_quote(text_ru: str) -> str:
    q = html.escape((text_ru or "").strip())
    return f"🌅 <b>Утренняя цитата</b>\n<blockquote>{q}</blockquote>"
# =========================
# IMAGE INBOX / SHRINK (vision economy)
# =========================
IMG_INBOX = DATA_DIR / "img_inbox"
IMG_INBOX.mkdir(parents=True, exist_ok=True)

def _shrink_jpeg_bytes(src_bytes: bytes, max_side: int = 1024, quality: int = 70) -> bytes:
    try:
        im = Image.open(io.BytesIO(src_bytes)).convert("RGB")
        w, h = im.size

        scale = min(1.0, max_side / float(max(w, h)))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=int(quality), optimize=True)
        return out.getvalue()

    except Exception:
        return src_bytes

def _extract_video_audio_mp3(video_bytes: bytes) -> bytes:
    import subprocess
    import tempfile

    if not video_bytes:
        return b""

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        video_path = d / "input.mp4"
        audio_path = d / "audio.mp3"
        video_path.write_bytes(video_bytes)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-t", "120",
            "-f", "mp3",
            str(audio_path),
        ]

        try:
            subprocess.run(cmd, check=False, timeout=45)
            if audio_path.exists() and audio_path.stat().st_size > 0:
                return audio_path.read_bytes()
        except Exception:
            return b""

    return b""

def _extract_video_frames(video_bytes: bytes, n: int = 5) -> list[bytes]:
    """
    Extract a few JPG frames from a Telegram video using ffmpeg.
    """
    import subprocess
    import tempfile

    frames: list[bytes] = []

    if not video_bytes:
        return frames

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        video_path = d / "input.mp4"
        out_pattern = d / "frame_%03d.jpg"

        video_path.write_bytes(video_bytes)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vf", "fps=0.25",
            "-frames:v", str(int(n)),
            "-q:v", "3",
            str(out_pattern),
        ]

        try:
            subprocess.run(cmd, check=False, timeout=30)
        except Exception:
            return frames

        for p in sorted(d.glob("frame_*.jpg"))[:int(n)]:
            try:
                frames.append(_shrink_jpeg_bytes(p.read_bytes(), max_side=768, quality=65))
            except Exception:
                pass

    return frames

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        return ""

    try:
        from pypdf import PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader
        except Exception:
            raise RuntimeError("PDF extractor missing: add pypdf to requirements.txt")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []

    for page in reader.pages[:30]:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = txt.strip()
        if txt:
            parts.append(txt)

    return "\n\n".join(parts).strip()

DOC_OCR_ENABLE = os.getenv("V_DOC_OCR_ENABLE", "1").strip().lower() in {"1", "true", "yes", "on"}
DOC_OCR_MAX_PAGES = int(os.getenv("V_DOC_OCR_MAX_PAGES", "5"))
def _looks_like_text_heavy_image(img_bytes: bytes) -> bool:
    """
    Cheap heuristic:
    text screenshots/docs usually compress much smaller
    and have large dimensions with low entropy.
    """
    try:
        from PIL import Image
        import io

        im = Image.open(io.BytesIO(img_bytes))
        w, h = im.size

        # tiny meme/photo
        if w < 500 or h < 300:
            return False

        # screenshots/docs are often PNG-ish and compact
        size_kb = len(img_bytes) / 1024.0

        # lots of text / white background
        if size_kb < 900 and (w * h) >= 700_000:
            return True

    except Exception:
        pass

    return False


def _ocr_image_bytes(img_bytes: bytes, *, filename: str = "image") -> str:
    if not img_bytes or not DOC_OCR_ENABLE:
        return ""

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return ""

    try:
        from openai import OpenAI

        b64 = base64.b64encode(img_bytes).decode("ascii")
        client = OpenAI()

        resp = client.responses.create(
            model=os.getenv("V_VISION_MODEL", os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты OCR-extractor. Извлеки весь видимый текст с изображения. "
                        "Не анализируй, не комментируй, не исправляй. "
                        "Сохрани порядок строк насколько возможно. "
                        "Если текста нет — верни пустую строку."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": f"Файл: {filename}. Извлеки текст."},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}",
                        },
                    ],
                },
            ],
        )

        return (getattr(resp, "output_text", "") or "").strip()

    except Exception as e:
        print(f"[ocr] image failed: {type(e).__name__}: {e}", flush=True)
        return ""


def _extract_pdf_ocr_text(pdf_bytes: bytes, *, filename: str = "document.pdf") -> str:
    if not pdf_bytes or not DOC_OCR_ENABLE:
        return ""

    try:
        import fitz  # PyMuPDF
    except Exception:
        raise RuntimeError("OCR PDF extractor missing: add pymupdf to requirements.txt")

    parts: list[str] = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_n = min(len(doc), max(1, DOC_OCR_MAX_PAGES))

        for i in range(pages_n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            img_bytes = pix.tobytes("jpg")
            txt = _ocr_image_bytes(img_bytes, filename=f"{filename} page {i + 1}")
            if txt:
                parts.append(f"--- page {i + 1} ---\n{txt}")

        doc.close()

    except Exception as e:
        print(f"[ocr] pdf failed: {type(e).__name__}: {e}", flush=True)

    return "\n\n".join(parts).strip()

def _extract_text_document(raw: bytes, filename: str, mime_type: str) -> str:
    fn = (filename or "").lower()
    mt = (mime_type or "").lower()

    if mt == "application/pdf" or fn.endswith(".pdf"):
        txt = _extract_pdf_text(raw)
        if len((txt or "").strip()) >= 100:
            return txt

        ocr_txt = _extract_pdf_ocr_text(raw, filename=filename or "document.pdf")
        return ocr_txt or txt

    if (
        mt.startswith("text/")
        or fn.endswith(".txt")
        or fn.endswith(".md")
        or fn.endswith(".csv")
        or fn.endswith(".json")
        or fn.endswith(".log")
    ):
        for enc in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                return raw.decode(enc).strip()
            except Exception:
                continue

    return ""

BASE_DIR = Path(__file__).resolve().parent
NEWS_SOURCES = BASE_DIR / "news_sources.txt"

DEFAULT_NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
DEFAULT_NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

BRAVE_SEARCH_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()

VOICE_STT_MODEL = os.getenv("V_VOICE_STT_MODEL", "gpt-4o-mini-transcribe")
VOICE_TTS_MODEL = os.getenv("V_VOICE_TTS_MODEL", "gpt-4o-mini-tts")
VOICE_TTS_VOICE = os.getenv("V_VOICE_TTS_VOICE", "verse")

ELEVENLABS_VOICE_ID = os.getenv(
    "ELEVENLABS_VOICE_ID",
    "EXAVITQu4vr4xnSDxMaL",
)

VOICE_REPLY_MODE = os.getenv("V_VOICE_REPLY_MODE", "text").strip().lower()

# optional: restrict to one chat
_CHAT_ID_ENV = (os.getenv("CHAT_ID") or "").strip()
ALLOWED_CHAT_ID: Optional[int] = int(_CHAT_ID_ENV) if _CHAT_ID_ENV else None

_ADMIN_USER_IDS_ENV = (os.getenv("ADMIN_USER_IDS") or "").strip()
ADMIN_USER_IDS: list[int] = []
if _ADMIN_USER_IDS_ENV:
    try:
        ADMIN_USER_IDS = [
            int(x.strip())
            for x in _ADMIN_USER_IDS_ENV.split(",")
            if x.strip()
        ]
    except Exception:
        ADMIN_USER_IDS = []

MAIN_GROUP_ID = -1002356524398

# one-shot relay mode: (chat_id, user_id) -> True
RELAY_NEXT_MESSAGE: dict[tuple[int, int], bool] = {}

# =========================
# BEAUTY DIALOG STATE
# =========================
BEAUTY_DIALOG_STATE = {}  # (chat_id, user_id) -> ts
BEAUTY_DIALOG_TTL_SEC = 300


def _is_admin_user(message: Message) -> bool:
    uid = int(message.from_user.id) if message.from_user else 0
    return bool(uid and uid in ADMIN_USER_IDS)


def _relay_key(message: Message) -> tuple[int, int]:
    return (
        int(message.chat.id),
        int(message.from_user.id) if message.from_user else 0,
    )


def _relay_is_armed(message: Message) -> bool:
    return RELAY_NEXT_MESSAGE.get(_relay_key(message), False)


def _relay_arm(message: Message) -> None:
    RELAY_NEXT_MESSAGE[_relay_key(message)] = True


def _relay_disarm(message: Message) -> None:
    RELAY_NEXT_MESSAGE.pop(_relay_key(message), None)


def _beauty_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return (int(chat_id), int(user_id))


def _beauty_activate(chat_id: int, user_id: int) -> None:
    BEAUTY_DIALOG_STATE[_beauty_key(chat_id, user_id)] = time.time()


def _beauty_is_active(chat_id: int, user_id: int) -> bool:
    ts = BEAUTY_DIALOG_STATE.get(_beauty_key(chat_id, user_id))

    if not ts:
        return False

    if (time.time() - ts) > BEAUTY_DIALOG_TTL_SEC:
        BEAUTY_DIALOG_STATE.pop(_beauty_key(chat_id, user_id), None)
        return False

    return True


def _beauty_followup(text: str) -> bool:
    t = (text or "").strip().lower()

    triggers = [
        "еще",
        "ещё",
        "другое",
        "другой",
        "давай еще",
        "давай ещё",
        "следующее",
        "продолжай",
        "не, другое",
        "другое давай",
        "что-нибудь другое",
        "еще красоты",
        "ещё красоты",
        "ну еще",
        "ну ещё",
    ]

    return any(x in t for x in triggers)

def _is_explicit_non_beauty_request(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" ?!.,:;")

    if not t:
        return False

    return bool(re.search(
        r"\b("
        r"фото|фотки|фотограф|картинк|изображен|"
        r"клип|клипы|youtube|ютуб|ролик|видео|"
        r"песня|трек|музык|offspring|off spring|"
        r"где купить|купить|цена|стоит|"
        r"найди|поищи|покажи|скинь|пришли|дай"
        r")\b",
        t,
        flags=re.I,
    )) and not bool(re.search(
        r"(?:красот\w*|красив\w*|эстетик\w*|вайб\w*)",
        t,
        flags=re.I,
    ))

def _looks_like_direct_beauty_request(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" ?!.,:;")

    if not t:
        return False

    return bool(re.search(
        r"\b(дай|покажи|пришли|скинь|подбери|хочу|сделай|порадуй)\b.*(?:красот\w*|красив\w*|эстетик\w*|вайб\w*)|"
        r"(?:красот\w*|красив\w*|эстетик\w*|вайб\w*).*\b(дай|покажи|пришли|скинь|подбери|хочу|сделай|порадуй)\b",
        t,
        flags=re.I,
    ))

def _make_beauty_caption(user_text: str, item: dict, chat_id: int = 0, user_id: int = 0) -> str:
    fallback_options = [
        "Вот. Красиво, но без восторженного кудахтанья.",
        "Красиво. Даже спорить не с чем.",
        "Редкий случай, когда картинка справилась без помощи людей.",
        "Нормально. Глазам можно выдать премию.",
        "Вот это уже похоже на порядок.",
    ]

    fallback = random.choice(fallback_options)

    try:
        if not (os.getenv("OPENAI_API_KEY") or "").strip():
            return fallback

        from openai import OpenAI

        client = OpenAI()

        meta = {
            "id": item.get("id"),
            "title": item.get("title"),
            "caption": item.get("caption"),
            "src": item.get("src"),
        }

        persona_prompt = ""
        irritation_prompt = ""

        try:
            persona_prompt = getattr(chatgpt_dialog.persona, "_SYSTEM_PROMPT", "").strip()
        except Exception:
            persona_prompt = ""

        try:
            irritation_prompt = chatgpt_dialog._irritation_instruction(chat_id, user_id)
        except Exception:
            irritation_prompt = ""

        system = (
            (persona_prompt or "Ты Веся: сухая, умная, ироничная, без блогерского восторга.")
            + "\n\n"
            + (irritation_prompt or "")
            + "\n\n"
            "Задача: написать короткую подпись к отправляемому beauty-видео.\n"
            "Это не обычный диалог и не описание видео. Это подпись к уже выбранному ролику.\n"
            "Сохрани общий стиль Веси: сухо, умно, слегка язвительно, но без пошлости.\n"
            "Каждый раз формулируй по-разному.\n"
            "Не используй готовые шаблоны и не повторяй старые подписи.\n"
            "Не пиши как блогер.\n"
            "Не используй фразы: 'красоту заказывали', 'сделала красиво', "
            "'лови', 'эстетика подъехала', 'почти искусство'.\n"
            "Не задавай вопросов пользователю.\n"
            "Не объясняй, что ты делаешь.\n"
            "Формат: одна короткая фраза, максимум 12 слов."
        )

        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": (
                        f"Запрос пользователя: {user_text}\n"
                        f"Метаданные ролика: {json.dumps(meta, ensure_ascii=False)}"
                    ),
                },
            ],
        )

        out = (getattr(resp, "output_text", "") or "").strip()
        out = re.sub(r"\s+", " ", out).strip()
        out = out.strip("\"'«» ")

        try:
            out = chatgpt_dialog.persona.postprocess_text(out, user_text)
        except Exception:
            pass

        return out[:180] or fallback

    except Exception:
        return fallback

def _relay_command_text(text: str) -> bool:
    t = (text or "").strip().lower()
    return "режим пересылки" in t or "отправь в основную группу" in t


async def _copy_to_main_group(message: Message) -> bool:
    try:
        await bot.copy_message(
            chat_id=MAIN_GROUP_ID,
            from_chat_id=int(message.chat.id),
            message_id=int(message.message_id),
        )
        return True
    except Exception as e:
        print(f"[relay] copy failed: {type(e).__name__}: {e}", flush=True)
        return False

def _is_relayable_document(message: Message) -> bool:
    doc = getattr(message, "document", None)
    if not doc:
        return False

    mt = (getattr(doc, "mime_type", "") or "").lower()
    return mt.startswith("image/") or mt.startswith("video/")

# =========================
# BOT
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _chat_allowed(message: Message) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return int(message.chat.id) == int(ALLOWED_CHAT_ID)


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[main] {ts} UTC {msg}", flush=True)

def _load_memory_events(limit: int = 500) -> list[dict]:
    candidates = []

    # 1) Явные имена из memory.py, если они есть
    for attr in (
        "MEMORY_EVENTS_PATH",
        "EVENTS_PATH",
        "EVENT_LOG_PATH",
        "MEMORY_LOG_PATH",
        "USER_EVENTS_PATH",
    ):
        try:
            p = getattr(vesya_memory, attr, None)
            if p:
                candidates.append(Path(p))
        except Exception:
            pass

    # 2) Частые варианты имён в DATA_DIR
    for name in (
        "vesya_memory_events.json",
        "memory_events.json",
        "events.json",
        "vesya_events.json",
        "user_events.json",
        "memory_log.json",
        "events.jsonl",
        "vesya_memory_events.jsonl",
        "memory_events.jsonl",
    ):
        candidates.append(DATA_DIR / name)

    # 3) Широкий fallback по /data
    try:
        candidates.extend(DATA_DIR.glob("*event*.json"))
        candidates.extend(DATA_DIR.glob("*events*.json"))
        candidates.extend(DATA_DIR.glob("*memory*.json"))
        candidates.extend(DATA_DIR.glob("*event*.jsonl"))
        candidates.extend(DATA_DIR.glob("*events*.jsonl"))
        candidates.extend(DATA_DIR.glob("*memory*.jsonl"))
    except Exception:
        pass

    seen = set()

    for p in candidates:
        try:
            p = Path(p)
            key = str(p)
            if key in seen or not p.exists() or not p.is_file():
                continue
            seen.add(key)

            if p.suffix.lower() == ".jsonl":
                rows = []
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            rows.append(obj)
                    except Exception:
                        pass

                rows = [
                    x for x in rows
                    if isinstance(x, dict)
                    and ("chat_id" in x or "user_id" in x)
                    and ("text" in x or "reply" in x)
                ]

                if rows:
                    return rows[-int(limit):]

            data = json.loads(p.read_text(encoding="utf-8"))

            if isinstance(data, dict):
                for key in ("events", "items", "rows", "messages"):
                    if isinstance(data.get(key), list):
                        data = data[key]
                        break

            if isinstance(data, list):
                rows = [
                    x for x in data
                    if isinstance(x, dict)
                    and ("chat_id" in x or "user_id" in x)
                    and ("text" in x or "reply" in x)
                ]

                if rows:
                    return rows[-int(limit):]

        except Exception:
            pass

    return []

def _admin_extract_id(text: str) -> int | None:
    m = re.search(r"(-?\d{5,})", text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def _admin_chat_list_text(limit: int = 20) -> str:
    events = _load_memory_events(3000)

    chats = {}

    for e in events:
        try:
            chat_id = int(e.get("chat_id") or 0)
            if not chat_id:
                continue

            row = chats.get(chat_id)
            if not row:
                row = {
                    "chat_id": chat_id,
                    "chat_type": e.get("chat_type") or "?",
                    "last_ts": "",
                    "last_text": "",
                    "last_reply": "",
                    "users": set(),
                    "count": 0,
                }
                chats[chat_id] = row

            uid = int(e.get("user_id") or 0)
            if uid:
                row["users"].add(uid)

            row["count"] += 1

            ts = str(e.get("ts") or "")
            if ts >= row["last_ts"]:
                row["last_ts"] = ts
                row["last_text"] = (e.get("text") or "").replace("\n", " ").strip()
                row["last_reply"] = (e.get("reply") or "").replace("\n", " ").strip()

        except Exception:
            pass

    rows = sorted(
        chats.values(),
        key=lambda x: x["last_ts"],
        reverse=True,
    )[:limit]

    if not rows:
        return "Активных чатов не нашла."

    out = [f"🗂 Активные чаты: {len(rows)}"]

    for i, row in enumerate(rows, start=1):
        text = row["last_text"]
        reply = row["last_reply"]

        if len(text) > 140:
            text = text[:140].rstrip() + "…"

        if len(reply) > 140:
            reply = reply[:140].rstrip() + "…"

        users_preview = ", ".join(
            str(x) for x in list(sorted(row["users"]))[:5]
        )

        if len(row["users"]) > 5:
            users_preview += f" +{len(row['users']) - 5}"

        ts = row["last_ts"][:19].replace("T", " ")

        out.append(
            "\n"
            f"{i}. {row['chat_type']}\n"
            f"chat_id: {row['chat_id']}\n"
            f"users: {len(row['users'])} [{users_preview or '-'}]\n"
            f"messages: {row['count']}\n"
            f"time: {ts or '-'}\n"
            f"U: {text or '-'}\n"
            f"V: {reply or '-'}"
        )

    out.append(
        "\nКоманды:\n"
        "• Веся покажи чат [chat_id]\n"
        "• Веся закрой чат [chat_id]\n"
        "• Веся игнор [user_id]"
    )

    return "\n".join(out)

def _admin_chat_tail_text(target_id: int, limit: int = 20) -> str:
    events = _load_memory_events(2000)
    rows = []

    for e in events:
        try:
            chat_id = int(e.get("chat_id") or 0)
            user_id = int(e.get("user_id") or 0)
            if chat_id == int(target_id) or user_id == int(target_id):
                rows.append(e)
        except Exception:
            pass

    rows = rows[-limit:]
    if not rows:
        return f"По {target_id} ничего не нашла."

    out = [f"Последние сообщения по {target_id}:"]
    for e in rows:
        text = (e.get("text") or "").replace("\n", " ")[:500]
        reply = (e.get("reply") or "").replace("\n", " ")[:500]
        out.append(f"\n[{e.get('ts','')}] user_id={e.get('user_id')} chat_id={e.get('chat_id')}")
        if text:
            out.append(f"U: {text}")
        if reply:
            out.append(f"V: {reply}")

    return "\n".join(out)

async def _try_admin_dialog_command(message: Message, text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()

    if not _is_admin_user(message):
        return False

    if re.search(r"\b(активные чаты|список чатов|последние чаты)\b", t):
        await _answer_long(message, _admin_chat_list_text())
        return True

    if re.search(r"\b(покажи чат|открой чат|история чата)\b", t):
        target_id = _admin_extract_id(t)
        if not target_id:
            await message.answer("ID чата или пользователя дай. Я не гадалка.")
            return True
        await _answer_long(message, _admin_chat_tail_text(target_id))
        return True

    if re.search(r"\b(закрой чат|сбрось чат|закрыть чат)\b", t):
        target_id = _admin_extract_id(t)
        if not target_id:
            await message.answer("ID дай.")
            return True

        try:
            chatgpt_dialog.end(target_id, target_id)
            chatgpt_dialog.end(int(message.chat.id), target_id)
        except Exception:
            pass

        await message.answer(f"Чат {target_id} закрыла.")
        return True

    if re.search(r"\b(игнор|забань|заблокируй)\b", t):
        target_id = _admin_extract_id(t)
        if not target_id:
            await message.answer("ID дай.")
            return True

        users = _load_blocked_users()
        users.add(int(target_id))
        _save_blocked_users(users)
        await message.answer(f"Пользователь {target_id} теперь в игноре.")
        return True

    if re.search(r"\b(разигнор|разбань|разблокируй)\b", t):
        target_id = _admin_extract_id(t)
        if not target_id:
            await message.answer("ID дай.")
            return True

        users = _load_blocked_users()
        users.discard(int(target_id))
        _save_blocked_users(users)
        await message.answer(f"Пользователь {target_id} снова доступен.")
        return True

    return False

def _record_memory_event(
    message: Message,
    *,
    text: str,
    intent: str = "",
    reply: str = "",
    event_type: str = "text",
) -> None:
    try:
        from datetime import datetime, timezone

        user = message.from_user
        if not user:
            return

        user_id = int(user.id)
        chat_id = int(message.chat.id)

        profiles = vesya_memory.load_profiles()
        profile = vesya_memory.ensure_user_profile(
            profiles,
            user_id=user_id,
            display_name=getattr(user, "full_name", "") or "",
            username=getattr(user, "username", "") or "",
        )

        if intent:
            vesya_memory.bump_intent(profile, intent)

        try:
            vesya_memory.update_user_facts_from_text(profile, text)
        except Exception as e:
            print(f"[memory] facts update failed: {type(e).__name__}: {e}", flush=True)

        vesya_memory.update_night_owl(
            profile,
            hour_local=datetime.now().hour,
        )

        vesya_memory.save_profiles(profiles)

        reply_to_message_id = 0
        try:
            if getattr(message, "reply_to_message", None):
                reply_to_message_id = int(message.reply_to_message.message_id)
        except Exception:
            reply_to_message_id = 0

        vesya_memory.append_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id,
            "chat_type": message.chat.type,
            "user_id": user_id,
            "user_name": getattr(user, "full_name", "") or "",
            "message_id": int(message.message_id),
            "reply_to_message_id": reply_to_message_id,
            "type": event_type,
            "intent": intent,
            "text": (text or "")[:2000],
            "reply": (reply or "")[:2000],
        })

        vesya_memory.prune_memory()

    except Exception as e:
        print(f"[memory] record failed: {type(e).__name__}: {e}", flush=True)

def _img_should_react(chat_id: int) -> bool:
    now = time.time()
    last = float(IMG_REACT_LAST_TS.get(int(chat_id), 0.0))

    # cooldown gate
    if (now - last) < IMG_REACT_COOLDOWN_SEC:
        return False

    # probability gate
    if random.random() > IMG_REACT_PROB:
        return False

    IMG_REACT_LAST_TS[int(chat_id)] = now
    return True

def _is_forwarded_message(message: Message) -> bool:
    return bool(
        getattr(message, "forward_origin", None)
        or getattr(message, "forward_date", None)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_sender_name", None)
        or getattr(message, "forward_from_chat", None)
    )

def _is_direct_group_address(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False

    return bool(re.match(
        r"^\s*(веся|веська|веслава|vesya|сергеевна)\s*[,.:;!\-]?\s+",
        t,
        flags=re.I,
    ))


def _is_reply_to_bot(message: Message) -> bool:
    r = getattr(message, "reply_to_message", None)
    return bool(
        r
        and getattr(r, "from_user", None)
        and r.from_user.is_bot
    )


def _group_message_addresses_vesya(message: Message, text: str) -> bool:
    if message.chat.type not in ("group", "supergroup"):
        print(
            "[GROUP_GATE] pass reason=not_group "
            f"chat_type={message.chat.type!r} text={(text or '')[:120]!r}",
            flush=True,
        )
        return True

    t = (text or "").strip()

    is_cmd = t.startswith("/")
    is_direct = _is_direct_group_address(t)
    is_reply_bot = _is_reply_to_bot(message)

    allowed = bool(is_cmd or is_direct or is_reply_bot)

    print(
        "[GROUP_GATE] "
        f"allowed={allowed} "
        f"chat_type={message.chat.type!r} "
        f"is_cmd={is_cmd} "
        f"is_direct={is_direct} "
        f"is_reply_bot={is_reply_bot} "
        f"text={t[:120]!r}",
        flush=True,
    )

    return allowed

def _wants_context_comment(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False

    # "как тебе / что думаешь / оцени" — это запрос на оценку объекта,
    # даже если внутри есть слово "тебе".
    if any(x in t for x in (
        "как тебе",
        "что думаешь",
        "что скажешь",
        "прокоммент",
        "оцени",
        "мнение",
    )):
        return True

    # Личный вопрос к Весе — отвечаем на вопрос, НЕ комментируем вложенный/пересланный объект.
    if re.search(
        r"\b(ты|тебя|твой|твоя|твои|хочешь|можешь|будешь|стала бы|согласна|нравится ли тебе)\b",
        t,
        flags=re.I,
    ):
        return False

    return any(x in t for x in (
        "как тебе",
        "что думаешь про это",
        "что думаешь об этом",
        "что скажешь про это",
        "что скажешь об этом",
        "прокоммент",
        "оцени",
        "мнение по этому",
        "ну и",
        "это как",
        "как оно",
        "разбери",
        "что по этому поводу",
        "что по нему",
        "что по ней",
        "как тебе такое",
    ))

async def _try_reply_context_comment(message: Message, user_text: str) -> bool:
    r = message.reply_to_message
    if not r:
        return False

    prompt = f"Веся, прокомментируй это в своём вкусе. Вопрос пользователя: {user_text}"

    try:
        if getattr(r, "photo", None):
            raw = await _download_tg_file_bytes(message.bot, r.photo[-1].file_id)
            img_bytes = _shrink_jpeg_bytes(raw)
            dd = chatgpt_dialog.describe_or_compare_photo(prompt, img_bytes)
            if dd and (dd.reply or "").strip():
                await _answer_long(message, dd.reply)
                return True

        doc = getattr(r, "document", None)
        if doc:
            mt = (getattr(doc, "mime_type", "") or "").lower()

            if mt.startswith("image/"):
                raw = await _download_tg_file_bytes(message.bot, doc.file_id)
                img_bytes = _shrink_jpeg_bytes(raw)
                dd = chatgpt_dialog.describe_or_compare_photo(prompt, img_bytes)
                if dd and (dd.reply or "").strip():
                    await _answer_long(message, dd.reply)
                    return True

            if mt.startswith("video/"):
                raw = await _download_tg_file_bytes(message.bot, doc.file_id)
                frames = await asyncio.to_thread(_extract_video_frames, raw, 5)
                audio_mp3 = await asyncio.to_thread(_extract_video_audio_mp3, raw)
                dd = chatgpt_dialog.describe_video_frames(prompt, frames, audio_mp3)
                if dd and (dd.reply or "").strip():
                    await _answer_long(message, dd.reply)
                    return True

            if mt == "application/pdf" or (getattr(doc, "file_name", "") or "").lower().endswith((".pdf", ".txt", ".md", ".csv", ".json", ".log")):
                raw = await _download_tg_file_bytes(message.bot, doc.file_id)
                extracted = await asyncio.to_thread(
                    _extract_text_document,
                    raw,
                    getattr(doc, "file_name", "") or "document",
                    mt,
                )
                dd = chatgpt_dialog.analyze_document_text(
                    user_text,
                    getattr(doc, "file_name", "") or "document",
                    extracted,
                )
                if dd and (dd.reply or "").strip():
                    await _answer_long(message, dd.reply)
                    return True

        if getattr(r, "video", None):
            raw = await _download_tg_file_bytes(message.bot, r.video.file_id)
            frames = await asyncio.to_thread(_extract_video_frames, raw, 5)
            audio_mp3 = await asyncio.to_thread(_extract_video_audio_mp3, raw)
            dd = chatgpt_dialog.describe_video_frames(prompt, frames, audio_mp3)
            if dd and (dd.reply or "").strip():
                await _answer_long(message, dd.reply)
                return True

        obj_text = (r.text or r.caption or "").strip()

        if obj_text:
            enriched_text = obj_text

            try:
                url = _extract_first_url(obj_text)

                if url:
                    page_text = await asyncio.to_thread(_fetch_url_text, url)

                    if page_text:
                        enriched_text = (
                            f"{obj_text}\n\n"
                            f"Содержимое страницы:\n"
                            f"{page_text[:8000]}"
                        )

            except Exception as e:
                print(f"[context_url] failed: {type(e).__name__}: {e}", flush=True)

            dd = chatgpt_dialog.comment_text_object(
                user_text,
                enriched_text,
            )

            if dd and (dd.reply or "").strip():
                await _answer_long(message, dd.reply)
                return True

    except Exception as e:
        print(f"[context_comment] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"не смогла прокомментировать: {type(e).__name__}: {e}")
        return True

    return False

def _pending_object_key(chat_id: int, user_id: int) -> tuple[int, int]:
    return (int(chat_id), int(user_id))


def _set_pending_message_object_request(chat_id: int, user_id: int, instruction: str) -> None:
    PENDING_MESSAGE_OBJECT_REQUEST[_pending_object_key(chat_id, user_id)] = {
        "instruction": (instruction or "").strip(),
        "ts": time.time(),
    }


def _pop_pending_message_object_request(chat_id: int, user_id: int) -> dict | None:
    key = _pending_object_key(chat_id, user_id)
    pending = PENDING_MESSAGE_OBJECT_REQUEST.pop(key, None)

    if not isinstance(pending, dict):
        return None

    ts = float(pending.get("ts") or 0)
    if not ts or (time.time() - ts) > PENDING_MESSAGE_OBJECT_TTL_SEC:
        return None

    return pending

def _add_object_part(parts: list[str], title: str, value: str, limit: int = 8000) -> None:
    v = (value or "").strip()
    if not v:
        return
    parts.append(f"--- {title} ---\n{v[:limit]}")


async def _build_message_object(message: Message, user_text: str) -> dict:
    """
    Universal MessageObject collector.

    Собирает всё, что реально есть в Telegram-сообщении:
    - текст / caption
    - forward / reply
    - URL + текст страницы
    - photo
    - image-document
    - text/pdf document
    """
    
    obj = {
        "user_text": (user_text or "").strip(),
        "parts": [],
        "photo_bytes": b"",
        "document_text": "",
        "document_name": "",
        "has_object": False,
        "has_external_object": False,
        "source_kind": "message",
    }

    parts = obj["parts"]

    current_text = (
        getattr(message, "text", None)
        or getattr(message, "caption", None)
        or ""
    ).strip()

    if current_text:
        _add_object_part(parts, "current_message_text", current_text)
        obj["has_object"] = True

    if _is_forwarded_message(message):
        _add_object_part(parts, "forwarded_message", "Сообщение переслано.")
        obj["has_object"] = True
        obj["has_external_object"] = True
        obj["source_kind"] = "forwarded_message"
    r = getattr(message, "reply_to_message", None)
    if r:
        reply_text = (
            getattr(r, "text", None)
            or getattr(r, "caption", None)
            or ""
        ).strip()

        current_text_l = current_text.lower()
        explicit_reply_object_request = bool(re.search(
            r"\b(переведи|перевод|что значит|что означает|прокоммент|разбери|оцени|что думаешь|как тебе)\b",
            current_text_l,
            flags=re.I,
        ))

        reply_from_bot = bool(getattr(getattr(r, "from_user", None), "is_bot", False))

        if reply_text and (explicit_reply_object_request or not reply_from_bot):
            _add_object_part(parts, "reply_message_text", reply_text)
            obj["has_object"] = True
            obj["has_external_object"] = True
            obj["source_kind"] = "reply_message"

        if getattr(r, "photo", None):
            try:
                raw = await _download_tg_file_bytes(message.bot, r.photo[-1].file_id)
                obj["photo_bytes"] = _shrink_jpeg_bytes(raw)
                _add_object_part(parts, "reply_photo", "В reply есть изображение.")
                obj["has_object"] = True
                obj["has_external_object"] = True
                obj["source_kind"] = "reply_photo"
            except Exception as e:
                print(f"[message_object] reply photo failed: {type(e).__name__}: {e}", flush=True)

        rdoc = getattr(r, "document", None)
        if rdoc:
            try:
                rmt = (getattr(rdoc, "mime_type", "") or "").lower()
                rfn = getattr(rdoc, "file_name", "") or "reply_document"

                raw = await _download_tg_file_bytes(message.bot, rdoc.file_id)

                if rmt.startswith("image/"):
                    obj["photo_bytes"] = _shrink_jpeg_bytes(raw)
                    _add_object_part(parts, "reply_image_document", f"В reply есть изображение-документ: {rfn}")
                    obj["has_object"] = True
                    obj["has_external_object"] = True
                    obj["source_kind"] = "reply_image_document"
                else:
                    extracted = await asyncio.to_thread(_extract_text_document, raw, rfn, rmt)
                    if extracted:
                        obj["document_text"] = extracted
                        obj["document_name"] = rfn
                        _add_object_part(parts, f"reply_document_text:{rfn}", extracted)
                        obj["has_object"] = True
                        obj["has_external_object"] = True
                        obj["source_kind"] = "reply_document"
            except Exception as e:
                print(f"[message_object] reply document failed: {type(e).__name__}: {e}", flush=True)

    if getattr(message, "photo", None):
        try:
            raw = await _download_tg_file_bytes(message.bot, message.photo[-1].file_id)
            obj["photo_bytes"] = _shrink_jpeg_bytes(raw)
            _add_object_part(parts, "current_photo", "В текущем сообщении есть изображение.")
            obj["has_object"] = True
            obj["has_external_object"] = True
            obj["source_kind"] = "photo"
        except Exception as e:
            print(f"[message_object] current photo failed: {type(e).__name__}: {e}", flush=True)

    doc = getattr(message, "document", None)
    if doc:
        try:
            mt = (getattr(doc, "mime_type", "") or "").lower()
            fn = getattr(doc, "file_name", "") or "document"

            raw = await _download_tg_file_bytes(message.bot, doc.file_id)

            if mt.startswith("image/"):
                obj["photo_bytes"] = _shrink_jpeg_bytes(raw)
                _add_object_part(parts, "current_image_document", f"В текущем сообщении есть изображение-документ: {fn}")
                obj["has_object"] = True
                obj["has_external_object"] = True
                obj["source_kind"] = "image_document"
            else:
                extracted = await asyncio.to_thread(_extract_text_document, raw, fn, mt)
                if extracted:
                    obj["document_text"] = extracted
                    obj["document_name"] = fn
                    _add_object_part(parts, f"current_document_text:{fn}", extracted)
                    obj["has_object"] = True
                    obj["has_external_object"] = True
                    obj["source_kind"] = "document"
        except Exception as e:
            print(f"[message_object] current document failed: {type(e).__name__}: {e}", flush=True)

    combined_text = "\n".join(parts)
    urls = []
    for m in _URL_RE.finditer(combined_text):
        u = (m.group(0) or "").rstrip(").,!?;:")
        if u and u not in urls:
            urls.append(u)

    for url in urls[:3]:
        _add_object_part(parts, "url", url)
        obj["has_object"] = True
        obj["has_external_object"] = True
        try:
            page_text = await asyncio.to_thread(_fetch_url_text, url)
            if page_text:
                _add_object_part(parts, f"url_page_text:{url}", page_text, limit=8000)
        except Exception as e:
            print(f"[message_object] url fetch failed: {type(e).__name__}: {e}", flush=True)

    return obj

def _message_object_text(obj: dict) -> str:
    parts = obj.get("parts") or []
    if not isinstance(parts, list):
        return ""
    return "\n\n".join(str(x) for x in parts if str(x).strip()).strip()


async def _run_translate_factcheck_for_message(message: Message, user_text: str, object_text: str) -> None:
    """
    Executor for semantic route translate_factcheck:
    1) isolate checked text;
    2) translate it;
    3) extract factual claims;
    4) search sources for claims;
    5) answer with translation + verification.
    """
    raw_user = (user_text or "").strip()
    raw_object = (object_text or "").strip()

    check_text = _strip_vesya_prefix(raw_user)

    if ":" in check_text:
        tail = check_text.split(":", 1)[1].strip()
        if len(tail) >= 20:
            check_text = tail

    if len(check_text) < 20:
        check_text = re.sub(r"(?m)^--- .*? ---\s*", "", raw_object).strip()

    if not check_text:
        await message.answer("Текст для проверки не нашла.")
        return

    if not BRAVE_SEARCH_API_KEY:
        await message.answer("Фактчек не подключён: нет BRAVE_SEARCH_API_KEY.")
        return

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        await message.answer("Фактчек не подключён: нет OPENAI_API_KEY.")
        return

    try:
        import requests
        from openai import OpenAI

        client = OpenAI()

        translation = chatgpt_dialog.translate_to_ru(check_text)

        claims_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты claim-extractor.\n"
                        "Извлеки из текста только проверяемые фактические утверждения.\n"
                        "Не извлекай просьбы пользователя, команды 'переведи', 'проверь', служебные фразы.\n"
                        "Для каждого claim дай короткий поисковый запрос без слов 'переведи' и 'проверь'.\n"
                        "Верни строго JSON:\n"
                        "{\"claims\":[{\"claim\":\"\",\"search_query\":\"\"}]}"
                    ),
                },
                {
                    "role": "user",
                    "content": check_text[:4000],
                },
            ],
        )

        raw_claims = (getattr(claims_resp, "output_text", "") or "").strip()
        try:
            m = re.search(r"\{.*\}", raw_claims, flags=re.S)
            claims_data = json.loads(m.group(0) if m else raw_claims)
        except Exception:
            claims_data = {"claims": []}

        claims = claims_data.get("claims") if isinstance(claims_data, dict) else []
        if not isinstance(claims, list):
            claims = []

        claims = [
            c for c in claims
            if isinstance(c, dict)
            and (c.get("claim") or "").strip()
        ][:5]

        if not claims:
            await _answer_long(
                message,
                "Перевод:\n"
                f"{translation}\n\n"
                "Проверяемых фактических утверждений в тексте не выделила."
            )
            return

        evidence = []

        def _search(q: str) -> dict:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                params={
                    "q": q,
                    "count": 6,
                    "search_lang": "ru",
                    "country": "RU",
                    "freshness": "pm",
                },
                timeout=20,
            )
            r.raise_for_status()
            return r.json()

        for c in claims:
            q = (c.get("search_query") or c.get("claim") or "").strip()
            q = re.sub(r"\b(переведи|проверь|подтверди|фактчек)\b", " ", q, flags=re.I)
            q = re.sub(r"\s+", " ", q).strip()[:220]

            if not q:
                continue

            try:
                data = await asyncio.to_thread(_search, q)
                results = (data.get("web") or {}).get("results") or []
            except Exception as e:
                print(f"[translate_factcheck] search failed: {type(e).__name__}: {e}", flush=True)
                results = []

            compact = []
            seen = set()

            for x in results[:6]:
                url = (x.get("url") or "").strip()
                if not url or url in seen:
                    continue

                seen.add(url)
                item = {
                    "title": (x.get("title") or "").strip(),
                    "url": url,
                    "description": (x.get("description") or "").strip(),
                    "age": (x.get("age") or "").strip(),
                    "page_age": (x.get("page_age") or "").strip(),
                }

                try:
                    page_text = await asyncio.to_thread(_fetch_url_text, url)
                    if page_text:
                        item["page_text"] = page_text[:3500]
                except Exception:
                    pass

                compact.append(item)

            evidence.append({
                "claim": c.get("claim"),
                "search_query": q,
                "results": compact,
            })

        verify_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты factcheck-executor Веси.\n"
                        "Отвечай только по evidence. Не используй память и догадки.\n"
                        "Нужно дать:\n"
                        "1) перевод;\n"
                        "2) список утверждений: подтверждено / не подтверждено / противоречит источникам;\n"
                        "3) источники.\n"
                        "Если источники не подтверждают claim — так и пиши, без вероятностного гадания.\n"
                        "Не добавляй служебные строки вроде 'уверенность high/low'.\n"
                        "Стиль: кратко, точно, без канцелярита."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Исходный текст:\n{check_text[:4000]}\n\n"
                        f"Перевод:\n{translation[:4000]}\n\n"
                        f"Evidence JSON:\n{json.dumps(evidence, ensure_ascii=False)[:45000]}"
                    ),
                },
            ],
        )

        final_text = (getattr(verify_resp, "output_text", "") or "").strip()
        if not final_text:
            final_text = "Фактчек выполнился, но итоговый ответ не собрался."

        _remember_topic(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            {
                "type": "translate_factcheck",
                "query": check_text[:500],
                "summary": final_text[:1200],
            },
        )

        await _answer_long(message, final_text)

    except Exception as e:
        print(f"[translate_factcheck] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"фактчек сломался: {type(e).__name__}: {e}")


def _should_use_universal_layer(message: Message, user_text: str, obj: dict) -> bool:
    # В группе не отвечаем на всё подряд.
    # Отвечаем только если есть явное обращение, reply на Весю,
    # команда или pending-object request.
    if message.chat.type in ("group", "supergroup"):
        if obj.get("has_pending_object_request"):
            return True

        return _group_message_addresses_vesya(message, user_text)

    # В личке любой текст/голос должен пройти semantic_context_route:
    # chat вернётся обратно в обычный dialog,
    # calendar уйдёт в handle_calendar_message.
    return True

async def _try_universal_message_layer(
    message: Message,
    user_text: str,
    *,
    event_type: str = "message",
) -> bool:
    """
    Single entry point for understanding any Telegram message object.

    Не решает через keywords.
    Сначала собирает MessageObject, потом semantic_context_route решает смысл.
    """
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    obj = await _build_message_object(message, user_text)

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    if _is_secretary_mode_active(chat_id, user_id):
        from vesya_tools.secretary.handler import handle_secretary_message

        try:
            result = await handle_secretary_message(message, user_text, object_text)
            if result:
                await _answer_long(message, result)
                return True
        except Exception as e:
            print(f"[secretary] failed: {type(e).__name__}: {e}", flush=True)
            return True

    pending_object = None
    if obj.get("has_external_object"):
        pending_object = _pop_pending_message_object_request(chat_id, user_id)
        if pending_object:
            user_text = (
                str(pending_object.get("instruction") or "").strip()
                or user_text
            )
            obj["user_text"] = user_text
            obj["has_pending_object_request"] = True
    object_text = _message_object_text(obj)

    if not _should_use_universal_layer(message, user_text, obj):
        return False

    topic = _get_topic(chat_id, user_id)

    # NEW external object MUST override old topic context.
    # Otherwise LLM starts blending previous discussion
    # with newly attached/replied/forwarded object.
    route_topic = topic

    if obj.get("has_external_object"):
        route_topic = None

    semantic_ctx = chatgpt_dialog.semantic_context_route(
        user_text,
        object_text=object_text,
        topic=route_topic,
    )

    print(
        "[message_object_route]",
        {
            "event_type": event_type,
            "source_kind": obj.get("source_kind"),
            "route": semantic_ctx.get("route"),
            "has_photo": bool(obj.get("photo_bytes")),
            "has_document": bool(obj.get("document_text")),
            "object_chars": len(object_text),
        },
        flush=True,
    )

    route = str(semantic_ctx.get("route") or "chat").strip().lower()

    if route == "calendar":
        calendar_text = re.sub(
            r"^.*?\b("
            r"напомни|напоминание|поставь\s+напоминание|добавь\s+в\s+календарь|запланируй|"
            r"мой\s+часовой\s+пояс|установи\s+мой\s+часовой\s+пояс|поставь\s+мой\s+часовой\s+пояс|"
            r"часовой\s+пояс|таймзона|timezone|time\s+zone|время\s+у\s+меня"
            r")\b",
            r"\1",
            user_text,
            count=1,
            flags=re.I,
        ).strip()

        calendar_message = message.model_copy(update={"text": calendar_text})

        if await handle_calendar_message(calendar_message, CALENDAR_STORAGE):
            print("[calendar] reminder created", flush=True)
            return True

        print("[calendar] route=calendar but handler returned false", flush=True)
        return False

    if route == "topic_followup" and topic and not object_text:
        dd = chatgpt_dialog.continue_topic_discussion(user_text, topic)
        if dd and (dd.reply or "").strip():
            await _answer_long(message, dd.reply)
            return True

    if route == "translate_factcheck":
        await _run_translate_factcheck_for_message(message, user_text, object_text)
        return True

    if route != "object_analysis":
        return False

    instruction = (
        semantic_ctx.get("instruction")
        or user_text
        or "Пойми и объясни это сообщение."
    )

    
    if obj.get("photo_bytes"):
        vision_prompt = (
            f"{instruction}\n\n"
            f"Контекст сообщения:\n{object_text[:5000]}"
        )
        dd = chatgpt_dialog.describe_or_compare_photo(
            vision_prompt,
            obj["photo_bytes"],
        )

    elif obj.get("document_text"):
        dd = chatgpt_dialog.analyze_document_text(
            instruction,
            obj.get("document_name") or "document",
            obj.get("document_text") or "",
        )

    else:
        dd = chatgpt_dialog.comment_text_object(
            instruction,
            object_text,
        )

    if dd and (dd.reply or "").strip():
        await _answer_long(message, dd.reply)

        _remember_topic(
            chat_id,
            user_id,
            {
                "type": "message_object",
                "source_kind": obj.get("source_kind") or "message",
                "user_prompt": user_text,
                "summary": dd.reply[:1200],
            },
        )

        return True

    return False

def _topic_key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"


def _load_topics() -> dict:
    try:
        if TOPIC_PATH.exists():
            data = json.loads(TOPIC_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_topics(data: dict) -> None:
    try:
        now = time.time()
        clean = {}
        for k, v in (data or {}).items():
            if not isinstance(v, dict):
                continue
            ts = float(v.get("created_at") or 0)
            if ts and (now - ts) <= TOPIC_TTL_SEC:
                clean[k] = v

        tmp = TOPIC_PATH.with_suffix(TOPIC_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TOPIC_PATH)
    except Exception:
        pass


def _remember_topic(chat_id: int, user_id: int, topic: dict) -> None:
    data = _load_topics()
    topic = dict(topic or {})
    topic["created_at"] = time.time()
    data[_topic_key(chat_id, user_id)] = topic
    _save_topics(data)


def _get_topic(chat_id: int, user_id: int) -> dict | None:
    data = _load_topics()
    topic = data.get(_topic_key(chat_id, user_id))
    if not isinstance(topic, dict):
        return None

    ts = float(topic.get("created_at") or 0)
    if not ts or (time.time() - ts) > TOPIC_TTL_SEC:
        data.pop(_topic_key(chat_id, user_id), None)
        _save_topics(data)
        return None

    return topic

def _looks_like_topic_followup(text: str) -> bool:
    """
    Deprecated.
    Topic continuation is decided by chatgpt_dialog.semantic_context_route().
    Kept only to avoid breaking old references.
    """
    return False

# =========================
# NEWS RUNNER (calls Telethon inside news_digest)
# =========================

def _strip_vesya_prefix(text: str) -> str:
    t = (text or "").strip()
    return re.sub(
        r"^\s*(веся|вися|веська|веслава|vesya|сергеевна)\s*[,.:;!\-]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()

def _looks_like_plain_dialog_followup(text: str) -> bool:
    """
    Plain conversation continuation.
    Must NOT be hijacked by saved topic/document/image context.
    """
    t = _strip_vesya_prefix(text)
    t = re.sub(r"\s+", " ", t).strip().lower()

    return bool(re.fullmatch(
        r"(а\s+)?("
        r"не понял(а)?|"
        r"не понял(а)?\s+объясни( подробн(ее|ей))?|"
        r"объясни( подробн(ее|ей))?|"
        r"поясни( подробн(ее|ей))?|"
        r"подробн(ее|ей)|"
        r"в каком смысле|"
        r"что значит|"
        r"что это значит"
        r")",
        t,
        flags=re.I,
    ))

async def _answer_long(message: Message, text: str, *, chunk_size: int = 3800) -> None:
    t = (text or "").strip()
    if not t:
        return

    while t:
        if len(t) <= chunk_size:
            await message.answer(t)
            return

        part = t[:chunk_size]
        cut = max(
            part.rfind("\n\n"),
            part.rfind("\n"),
            part.rfind(". "),
            part.rfind("! "),
            part.rfind("? "),
            part.rfind("; "),
        )
        # убрать минимальный порог, всегда брать найденный cut
        if cut == -1:
            cut = chunk_size

        part = t[:cut].rstrip()
        rest = t[cut:].lstrip()
        await message.answer(part)
        t = rest

def _transcribe_voice_ogg(audio_bytes: bytes) -> str:
    if not audio_bytes:
        return ""

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return ""

    try:
        from openai import OpenAI
        from io import BytesIO

        client = OpenAI()

        f = BytesIO(audio_bytes)
        f.name = "voice.ogg"

        tr = client.audio.transcriptions.create(
            model=VOICE_STT_MODEL,
            file=f,
        )

        return (getattr(tr, "text", "") or "").strip()

    except Exception as e:
        print(f"[voice] transcribe failed: {type(e).__name__}: {e}", flush=True)
        return ""
    
def _tts_to_ogg_bytes(text: str) -> bytes:
    t = (text or "").strip()
    if not t:
        return b""

    t = t[:900]

    eleven_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not eleven_key:
        return b""

    proxy_url = (os.getenv("ELEVENLABS_PROXY") or "").strip() or None

    try:
        import httpx

        with httpx.Client(proxy=proxy_url, timeout=45) as client:
            try:
                ip = client.get("https://api.ipify.org").text.strip()
                print(f"[voice] elevenlabs outbound_ip={ip}", flush=True)
            except Exception as e:
                print(f"[voice] outbound_ip check failed: {type(e).__name__}: {e}", flush=True)

            r = client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                params={"output_format": "opus_48000_128"},
                headers={
                    "xi-api-key": eleven_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": t,
                    "model_id": "eleven_multilingual_v2",
                },
            )

            r.raise_for_status()
            return r.content

    except Exception as e:
        print(f"[voice] elevenlabs tts failed: {type(e).__name__}: {e}", flush=True)
        return b""

async def _send_reply_for_event(message: Message, reply: str, *, event_type: str = "text") -> None:
    r = (reply or "").strip()
    if not r:
        return

    if event_type == "voice" and VOICE_REPLY_MODE == "voice":
        audio = await asyncio.to_thread(_tts_to_ogg_bytes, r)

        if audio:
            tmp_path = f"/tmp/vesya_voice_{uuid.uuid4().hex}.ogg"
            try:
                Path(tmp_path).write_bytes(audio)
                await message.answer_voice(
                    FSInputFile(tmp_path),
                    caption=None,
                )
                return
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        # fallback если TTS не сработал
        await _answer_long(message, r)
        return

    await _answer_long(message, r)

def _looks_like_calendar_view_request(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = re.sub(r"\s+", " ", t).strip(" ?!.,:;")

    return bool(re.search(
        r"\b(покажи|проверь|что у меня|какие|список)\b.*\b(календарь|напоминан|дела)\b|"
        r"\b(календарь|напоминан|дела)\b.*\b(сегодня|завтра|на сегодня|на завтра)\b",
        t,
        flags=re.I,
    ))


def _calendar_view_range(text: str):
    import sqlite3
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    t = _strip_vesya_prefix(text).strip().lower()
    tz = ZoneInfo(os.getenv("V_RUNTIME_TZ", "Europe/Moscow"))
    now = datetime.now(tz)

    if "завтра" in t:
        day = now.date() + timedelta(days=1)
    else:
        day = now.date()

    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)

    return start, end


def _calendar_view_text(chat_id: int, user_id: int, text: str) -> str:
    import sqlite3
    from datetime import datetime

    start, end = _calendar_view_range(text)
    db_path = DATA_DIR / "vesya_calendar.sqlite3"

    if not db_path.exists():
        return "Календарь пуст. База напоминаний ещё не создана."

    rows = []

    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row

        tables = [
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]

        if not tables:
            con.close()
            return "Календарь пуст. Таблиц напоминаний в базе нет."

        # Ищем таблицу календаря по реальным колонкам, а не по придуманному имени.
        target = None
        columns = []

        for table in tables:
            cols = con.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = [str(c["name"]) for c in cols]

            has_time = any(c in col_names for c in ("remind_at", "run_at", "due_at", "event_at", "dt", "datetime", "time"))
            has_text = any(c in col_names for c in ("text", "title", "message", "body", "description"))
            has_chat = "chat_id" in col_names

            if has_time and has_text and has_chat:
                target = table
                columns = col_names
                break

        if not target:
            con.close()
            return "Календарь не прочитала: не нашла таблицу с chat_id, временем и текстом."

        time_col = next(c for c in ("remind_at", "run_at", "due_at", "event_at", "dt", "datetime", "time") if c in columns)
        text_col = next(c for c in ("text", "title", "message", "body", "description") if c in columns)

        done_col = next((c for c in ("done", "sent", "is_done", "completed") if c in columns), None)
        user_filter = " AND user_id = ?" if "user_id" in columns else ""
        done_filter = f" AND COALESCE({done_col}, 0) = 0" if done_col else ""

        sql = (
            f"SELECT * FROM {target} "
            f"WHERE chat_id = ?{user_filter} "
            f"AND {time_col} >= ? AND {time_col} < ?"
            f"{done_filter} "
            f"ORDER BY {time_col} ASC"
        )

        params = [int(chat_id)]
        if "user_id" in columns:
            params.append(int(user_id))

        params.extend([
            start.isoformat(),
            end.isoformat(),
        ])

        rows = con.execute(sql, params).fetchall()
        con.close()

    except Exception as e:
        return f"Календарь не прочитала: {type(e).__name__}: {e}"

    label = "завтра" if "завтра" in _strip_vesya_prefix(text).lower() else "сегодня"

    if not rows:
        return f"На {label} напоминаний не вижу."

    out = [f"Напоминания на {label}:"]

    for r in rows:
        raw_time = str(r[time_col])
        item_text = str(r[text_col] or "").strip() or "напоминание"

        try:
            dt = datetime.fromisoformat(raw_time)
            hhmm = dt.strftime("%H:%M")
        except Exception:
            hhmm = raw_time

        out.append(f"- {hhmm} — {item_text}")

    return "\n".join(out)

def _extract_web_search_query(text: str) -> str:
    q = _strip_vesya_prefix(text)
    q = re.sub(
        r"^\s*(найди|поищи|загугли|посмотри в интернете|поищи в интернете)\s+",
        "",
        q,
        flags=re.I,
    ).strip()
    return q


_URL_RE = re.compile(r"https?://[^\s<>\"']+", flags=re.I)


def _extract_first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(").,!?;:")


def _looks_like_url_followup(text: str) -> bool:
    t = _strip_vesya_prefix(text).strip().lower()
    t = t.strip(" ?!.,:;")
    return t in {
        "что это",
        "что это такое",
        "прочитай",
        "прочитай объявление",
        "перескажи",
        "перескажи что видишь",
        "прокомментируй",
        "расскажи суть",
        "суть",
    }


def _fetch_url_text(url: str) -> str:
    try:
        import requests

        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            },
            timeout=20,
        )

        if not r.ok:
            return ""

        text = r.text or ""
        text = re.sub(r"(?is)<script.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?</noscript>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:12000]

    except Exception:
        return ""


async def _run_url_read_for_message(message: Message, url: str, user_request: str) -> None:
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    page_text = await asyncio.to_thread(_fetch_url_text, url)

    if not page_text:
        await message.answer(
            "Я вижу ссылку, но страницу прочитать не смогла. "
            "Без текста страницы пересказывать не буду — иначе опять начнётся гадание."
        )
        return

    try:
        from openai import OpenAI

        client = OpenAI()
        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты — Веся. Пользователь дал ссылку и просит понять/прочитать страницу.\n"
                        "Отвечай только по переданному тексту страницы.\n"
                        "Если в тексте нет данных объявления — прямо скажи, что страница не дала содержимого.\n"
                        "Не используй память, историю, догадки и внешние факты.\n"
                        "Формат: что это / суть объявления / важные детали.\n"
                        "Кратко, без воды."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Запрос пользователя: {user_request}\n\n"
                        f"URL: {url}\n\n"
                        f"Текст страницы:\n{page_text}"
                    ),
                },
            ],
        )

        answer = (getattr(resp, "output_text", "") or "").strip()
        if not answer:
            answer = "Страницу прочитала, но внятной сути из текста не получилось вытащить."

        await _answer_long(message, answer)

    except Exception as e:
        print(f"[url_read] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"Ссылку прочитать не вышло: {type(e).__name__}: {e}")

async def _run_web_search_for_message(message: Message, query: str) -> None:
    query = (query or "").strip()
    query = re.sub(r"\s+", " ", query).strip()
    query = query[:250]
    if not query:
        await message.answer("Искать нечего. Пустой запрос — тоже диагноз.")
        return

    if not BRAVE_SEARCH_API_KEY:
        await message.answer("Поиск не подключён: нет BRAVE_SEARCH_API_KEY.")
        return

    try:
        import requests
        from openai import OpenAI

        def _search():
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                params={
                    "q": query,
                    "count": 20,
                    "search_lang": "ru",
                    "country": "RU",
                    "freshness": "pm",
                },
                timeout=20,
            )
            r.raise_for_status()
            return r.json()

        data = await asyncio.to_thread(_search)
        results = (data.get("web") or {}).get("results") or []

        if not results:
            await message.answer("Ничего не нашла.")
            return

        compact = []
        seen_urls = set()

        for x in results[:20]:
            url = (x.get("url") or "").strip()
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            compact.append({
                "title": (x.get("title") or "").strip(),
                "url": url,
                "description": (x.get("description") or "").strip(),
                "age": (x.get("age") or "").strip(),
                "page_age": (x.get("page_age") or "").strip(),
                "profile": x.get("profile") or {},
            })

        if not compact:
            await message.answer("Нашла пустую выдачу. Подтверждать нечего.")
            return

        for item in compact[:5]:
            try:
                page_text = await asyncio.to_thread(_fetch_url_text, item["url"])
                if page_text:
                    item["page_text"] = page_text[:6000]
            except Exception as e:
                print(f"[web_search] page fetch failed: {type(e).__name__}: {e}", flush=True)

        client = OpenAI()
        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты — проверочный web-search executor Веси.\n"
                        "Твоя задача — проверить фактическую базу по выдаче. Если пользователь просит прогноз — дать его отдельно как мнение Веси.\n\n"
                        "Правила:\n"
                        "1. Факты бери только из переданных результатов поиска.\n"
                        "2. Не используй память, прошлый диалог и общие знания для подтверждения фактов.\n"
                        "3. Для актуальных фактов, спорта, дат, победителей, цен, событий, новостей и персон желательно 2 независимых источника.\n"
                        "4. Если источников меньше двух, данные старые, источник не совпадает с вопросом или есть противоречия — verified=false.\n"
                        "5. Если пользователь НЕ просит прогноз и verified=false — НЕ формулируй предположение и НЕ давай вероятностный ответ.\n"
                        "6. Если пользователь просит прогноз, НЕ ищи готовый прогноз в источниках. Источники подтверждают только фактическую базу: группы, участников, расписание, сетку.\n"
                        "7. Для прогнозного запроса: сначала кратко дай подтверждённые факты, потом отдельно дай прогноз как мнение Веси. Прогноз не требует отдельного источника, но должен опираться на найденную фактическую базу.\n"
                        "8. Если фактической базы по выдаче нет вообще — прогноз не давай.\n\n"
                        "Верни строго JSON:\n"
                        "{"
                        "\"verified\": true/false,"
                        "\"confidence\": \"high|medium|low\","
                        "\"answer\": \"короткий ответ или причина неподтверждения\","
                        "\"source_count\": 0,"
                        "\"sources\": [{\"title\":\"\",\"url\":\"\",\"supports\":\"\"}],"
                        "\"conflicts\": \"описание противоречий или пусто\""
                        "}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Запрос пользователя: {query}\n\n"
                        f"Результаты поиска JSON:\n{json.dumps(compact, ensure_ascii=False)}"
                    ),
                },
            ],
        )

        raw = (getattr(resp, "output_text", "") or "").strip()

        try:
            raw_json = raw
            m = re.search(r"\{.*\}", raw_json, flags=re.S)
            if m:
                raw_json = m.group(0)
            verdict = json.loads(raw_json)
        except Exception:
            verdict = {
                "verified": False,
                "confidence": "low",
                "answer": "Поиск вернул данные, но проверочный разбор не удалось разобрать. Уверенно отвечать не буду.",
                "source_count": 0,
                "sources": [],
                "conflicts": "",
            }

        verified = bool(verdict.get("verified"))
        source_count = int(verdict.get("source_count") or 0)
        answer = (verdict.get("answer") or "").strip()
        confidence = (verdict.get("confidence") or "low").strip()
        conflicts = (verdict.get("conflicts") or "").strip()
        sources = verdict.get("sources") or []

        valid_sources = []
        seen = set()

        for src in sources:
            if not isinstance(src, dict):
                continue

            url = (src.get("url") or "").strip()
            title = (src.get("title") or "").strip()
            supports = (src.get("supports") or "").strip()

            if not url or url in seen:
                continue

            seen.add(url)
            valid_sources.append({
                "title": title,
                "url": url,
                "supports": supports,
            })

        is_forecast_query = bool(re.search(
            r"\b(прогноз|спрогнозируй|кто выйдет|кто победит|кто пройдет|кто пройдёт|шансы|фаворит|сценарий)\b",
            query,
            flags=re.I,
        ))

        if is_forecast_query and answer and len(valid_sources) >= 1:
            forecast_resp = client.responses.create(
                model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — Веся.\n"
                            "Пользователь просит прогноз по спортивному турниру.\n"
                            "Факты уже проверены отдельным web-search executor.\n"
                            "Твоя задача — НЕ перепроверять источники и НЕ отказываться из-за того, "
                            "что в источниках нет готового прогноза.\n"
                            "Сначала кратко обозначь фактическую базу, затем дай свой прогноз как мнение.\n"
                            "Прогноз отделяй от фактов.\n"
                            "Не пиши канцеляритом.\n"
                            "Не говори 'недостаточно источников для прогноза', если есть список групп или участников.\n"
                            "Если часть данных неполная — прямо скажи это, но всё равно дай осторожный прогноз по имеющейся базе.\n"
                            "Стиль: сухо, умно, с лёгкой иронией."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Запрос пользователя:\n{query}\n\n"
                            f"Проверочный вывод фактчекера:\n{answer}\n\n"
                            f"Найденные источники:\n{json.dumps(valid_sources, ensure_ascii=False)}\n\n"
                            f"Материалы поиска:\n{json.dumps(compact, ensure_ascii=False)[:45000]}"
                        ),
                    },
                ],
            )

            final_text = (getattr(forecast_resp, "output_text", "") or "").strip()
            if not final_text:
                final_text = answer or "Факты нашла, но прогноз сформулировать не вышло."

            final_lines = [final_text]

            if conflicts:
                print(f"[web_search][conflicts] {conflicts}", flush=True)

            print(
                f"[web_search][verified] sources={source_count} confidence={confidence}",
                flush=True,
            )

            final_lines.append("")
            final_lines.append("Источники:")
            for src in valid_sources[:3]:
                title = src["title"] or "Источник"
                final_lines.append(f"- {title}: {src['url']}")

            final_text = "\n".join(final_lines).strip()

        else:
            final_lines = [
                answer or "Точный ответ по найденной выдаче не подтверждён: не набралось двух независимых источников."
                "",
                f"Проверка: источников для уверенного ответа — {len(valid_sources)}; уверенность: {confidence}.",
            ]

            if conflicts:
                final_lines.append(f"Противоречия: {conflicts}")

            if valid_sources:
                final_lines.append("")
                final_lines.append("Что нашла:")
                for src in valid_sources[:3]:
                    title = src["title"] or "Источник"
                    final_lines.append(f"- {title}: {src['url']}")

            final_text = "\n".join(final_lines).strip()

        if not is_forecast_query:
            try:
                final_text = chatgpt_dialog.polish_research_reply(
                    int(message.chat.id),
                    int(message.from_user.id) if message.from_user else 0,
                    query,
                    final_text,
                )
            except Exception:
                pass

        _remember_topic(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            {
                "type": "web_search",
                "query": query,
                "summary": final_text[:1200],
            },
        )

        await _answer_long(message, final_text)
        
    except Exception as e:
        print(f"[web_search] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"поиск сломался: {type(e).__name__}: {e}")

def _research_norm_text(value: str) -> str:
    t = (value or "").strip().lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^\wа-яё0-9\s-]+", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _research_int(value) -> int:
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        s = str(value or "").strip()
        m = re.search(r"\d+", s)
        return max(0, int(m.group(0))) if m else 0
    except Exception:
        return 0


def _research_record_key(rec: dict) -> str:
    date = _research_norm_text(str(rec.get("date") or ""))
    location = _research_norm_text(str(rec.get("location") or ""))
    event = _research_norm_text(str(rec.get("event") or ""))

    # universal but conservative: same date + same place = likely same countable incident
    if date and location:
        return f"{date}|{location}"

    # fallback when date/location missing
    return f"{date}|{location}|{event[:90]}"


def _research_aggregate_records(records: list[dict], *, official_only: bool = False) -> dict:
    allowed_sources = {"official", "media_with_official_reference"}
    allowed_conf = {"high", "medium"}

    grouped: dict[str, dict] = {}
    rejected = 0

    for rec in records or []:
        if not isinstance(rec, dict):
            rejected += 1
            continue

        value = _research_int(rec.get("metric_value"))
        if value <= 0:
            rejected += 1
            continue

        source_type = str(rec.get("source_type") or "unknown").strip().lower()
        confidence = str(rec.get("confidence") or "low").strip().lower()

        if confidence not in allowed_conf:
            rejected += 1
            continue

        if official_only and source_type not in allowed_sources:
            rejected += 1
            continue

        key = _research_record_key(rec)
        if not key:
            rejected += 1
            continue

        current = grouped.get(key)
        if not current:
            grouped[key] = {
                "date": str(rec.get("date") or "").strip(),
                "location": str(rec.get("location") or "").strip(),
                "event": str(rec.get("event") or "").strip(),
                "metric_value": value,
                "metric_label": str(rec.get("metric_label") or "").strip(),
                "source_type": source_type,
                "confidence": confidence,
                "urls": [],
                "evidence": str(rec.get("evidence") or "").strip(),
            }
        else:
            # If the same incident appears with different counts, keep the highest
            # value as the latest/most complete count, not sum of duplicates.
            if value > int(current.get("metric_value") or 0):
                current["metric_value"] = value
                current["event"] = str(rec.get("event") or current.get("event") or "").strip()
                current["evidence"] = str(rec.get("evidence") or current.get("evidence") or "").strip()

            # upgrade confidence/source if better
            if current.get("confidence") != "high" and confidence == "high":
                current["confidence"] = "high"
            if current.get("source_type") != "official" and source_type == "official":
                current["source_type"] = "official"

        url = str(rec.get("source_url") or "").strip()
        if url and url not in grouped[key]["urls"]:
            grouped[key]["urls"].append(url)

    items = list(grouped.values())
    items.sort(key=lambda x: (x.get("date") or "9999-99-99", x.get("location") or ""))

    return {
        "items": items,
        "total": sum(int(x.get("metric_value") or 0) for x in items),
        "accepted": len(items),
        "rejected": rejected,
    }


def _research_format_result(query: str, aggregate: dict, *, source_policy: str = "") -> str:
    items = aggregate.get("items") or []
    total = int(aggregate.get("total") or 0)
    accepted = int(aggregate.get("accepted") or 0)
    rejected = int(aggregate.get("rejected") or 0)

    if not items:
        return (
            "Итог: подтверждённых счётных фактов по найденным материалам не вытащила.\n\n"
            f"Запрос: {query}\n"
            f"Отброшено слабых/неполных записей: {rejected}."
        )

    lines = []
    lines.append(f"Итог: {total}.")
    lines.append(f"Учтено уникальных записей: {accepted}.")
    if rejected:
        lines.append(f"Отброшено слабых/неполных записей: {rejected}.")
    lines.append("")

    lines.append("Список:")
    for i, rec in enumerate(items, start=1):
        date = rec.get("date") or "дата не указана"
        location = rec.get("location") or "место не указано"
        value = int(rec.get("metric_value") or 0)
        event = rec.get("event") or "событие не описано"
        source_type = rec.get("source_type") or "unknown"
        confidence = rec.get("confidence") or "unknown"

        lines.append(f"{i}) {date} — {location}")
        lines.append(f"   Число: {value}")
        lines.append(f"   Событие: {event}")
        lines.append(f"   Источник: {source_type}, уверенность: {confidence}")

        urls = rec.get("urls") or []
        if urls:
            lines.append(f"   Ссылка: {urls[0]}")
        lines.append("")

    all_urls = []
    for rec in items:
        for url in rec.get("urls") or []:
            if url and url not in all_urls:
                all_urls.append(url)

    if all_urls:
        lines.append("Источники:")
        for url in all_urls[:10]:
            lines.append(f"- {url}")
        lines.append("")

    if source_policy == "official_only":
        lines.append("Оговорка: итог только по записям, где источник отмечен как official или media_with_official_reference.")
    else:
        lines.append("Оговорка: итог только по найденным и извлечённым записям, без гарантии полного государственного реестра.")

    return "\n".join(lines).strip()

def _aggregate_claim_value(value) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            return int(value) if value >= 0 else None

        s = str(value or "").replace(" ", "").replace("\u00a0", "")
        m = re.search(r"\d+", s)
        if not m:
            return None
        return int(m.group(0))
    except Exception:
        return None


def _aggregate_source_weight(source_type: str) -> float:
    st = (source_type or "").strip().lower()
    weights = {
        "official": 1.0,
        "international_org": 0.9,
        "major_media": 0.75,
        "expert_estimate": 0.65,
        "media": 0.55,
        "telegram": 0.30,
        "unknown": 0.20,
    }
    return weights.get(st, 0.20)


def _aggregate_conf_weight(confidence: str) -> float:
    c = (confidence or "").strip().lower()
    if c == "high":
        return 1.0
    if c == "medium":
        return 0.65
    return 0.30


def _aggregate_format_claims(query: str, claims: list[dict]) -> str:
    clean = []

    for claim in claims or []:
        if not isinstance(claim, dict):
            continue

        value = _aggregate_claim_value(claim.get("value"))
        if value is None:
            continue

        source_type = str(claim.get("source_type") or "unknown").strip().lower()
        confidence = str(claim.get("confidence") or "low").strip().lower()
        score = _aggregate_source_weight(source_type) * _aggregate_conf_weight(confidence)

        clean.append({
            "value": value,
            "value_text": str(claim.get("value_text") or value).strip(),
            "metric": str(claim.get("metric") or "").strip(),
            "scope": str(claim.get("scope") or "").strip(),
            "period": str(claim.get("period") or "").strip(),
            "source_name": str(claim.get("source_name") or "").strip(),
            "source_type": source_type,
            "confidence": confidence,
            "url": str(claim.get("source_url") or "").strip(),
            "evidence": str(claim.get("evidence") or "").strip(),
            "score": score,
        })

    if not clean:
        return (
            "Итог: в найденных материалах не вытащила пригодную агрегированную цифру.\n\n"
            f"Запрос: {query}\n"
            "Это не список инцидентов, тут нужен источник с уже опубликованной общей статистикой."
        )

    clean.sort(key=lambda x: x["score"], reverse=True)

    high = [x for x in clean if x["score"] >= 0.55]
    basis = high or clean

    values = [int(x["value"]) for x in basis]
    min_v = min(values)
    max_v = max(values)
    best = basis[0]

    lines = []
    if min_v == max_v:
        lines.append(f"Итог: {min_v}.")
    else:
        lines.append(f"Итог: найденный диапазон оценок — от {min_v} до {max_v}.")
        lines.append(f"Наиболее сильная найденная оценка: {best['value_text']}.")

    lines.append("")
    lines.append("Оценки по источникам:")

    for i, item in enumerate(clean[:8], start=1):
        name = item["source_name"] or item["source_type"]
        metric = item["metric"] or "показатель"
        period = item["period"] or "период не указан"

        lines.append(f"{i}) {item['value_text']} — {name}")
        lines.append(f"   Метрика: {metric}")
        lines.append(f"   Период: {period}")
        lines.append(f"   Тип: {item['source_type']}, уверенность: {item['confidence']}")
        if item["evidence"]:
            lines.append(f"   Основание: {item['evidence'][:220]}")
        if item["url"]:
            lines.append(f"   Ссылка: {item['url']}")
        lines.append("")

    urls = []
    for item in clean:
        if item["url"] and item["url"] not in urls:
            urls.append(item["url"])

    if urls:
        lines.append("Источники:")
        for url in urls[:10]:
            lines.append(f"- {url}")
        lines.append("")

    lines.append("Оговорка: это агрегированные оценки из найденных источников, а не сумма отдельных инцидентов.")
    return "\n".join(lines).strip()

async def _run_research_count_for_message(message: Message, query: str) -> None:
    """
    Universal multi-source research/count executor.
    It is intentionally separate from quick web_search:
    - builds multiple search queries;
    - fetches several search result pages;
    - extracts countable records;
    - deduplicates incidents;
    - returns a sourced table and total.
    """
    query = (query or "").strip()
    query = re.sub(r"\s+", " ", query).strip()
    query = query[:500]

    if not query:
        await message.answer("Считать нечего. Пустой запрос — плохой объект расследования.")
        return

    if not BRAVE_SEARCH_API_KEY:
        await message.answer("Сложный поиск не подключён: нет BRAVE_SEARCH_API_KEY.")
        return

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        await message.answer("Сложный поиск не подключён: нет OPENAI_API_KEY.")
        return

    try:
        import requests
        from openai import OpenAI

        max_queries = int(os.getenv("V_RESEARCH_COUNT_QUERIES", "6"))
        max_results_per_query = int(os.getenv("V_RESEARCH_COUNT_RESULTS_PER_QUERY", "10"))
        max_pages = int(os.getenv("V_RESEARCH_COUNT_FETCH_PAGES", "12"))

        client = OpenAI()

        plan_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты планировщик универсального фактологического поиска.\n"
                        "Нужно превратить запрос пользователя в несколько поисковых запросов.\n"
                        "Не отвечай пользователю.\n"
                        "Верни только JSON.\n\n"
                        "JSON:\n"
                        "{"
                        "\"objective\":\"что считаем\","
                        "\"metric\":\"что суммировать\","
                        "\"source_policy\":\"official_only|open_sources\","
                        "\"queries\":[\"query1\",\"query2\"]"
                        "}\n\n"
                        "Правила:\n"
                        "- Не зашивай частный домен.\n"
                        "- Если пользователь просит официально — source_policy=official_only.\n"
                        "- Делай разные формулировки запроса: по событию, по метрике, по периоду, по официальным сообщениям.\n"
                        "- Максимум 6 queries.\n"
                    ),
                },
                {"role": "user", "content": query},
            ],
        )

        plan_text = (getattr(plan_resp, "output_text", "") or "").strip()
        try:
            plan = json.loads(re.search(r"\{.*\}", plan_text, flags=re.DOTALL).group(0))
        except Exception:
            plan = {}

        search_queries = plan.get("queries") if isinstance(plan.get("queries"), list) else []
        search_queries = [str(x).strip() for x in search_queries if str(x).strip()]
        if not search_queries:
            search_queries = [query]

        search_queries = search_queries[:max_queries]

        def _brave_search(q: str) -> list[dict]:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                params={
                    "q": q,
                    "count": max_results_per_query,
                    "search_lang": "ru",
                    "country": "RU",
                },
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            return (data.get("web") or {}).get("results") or []

        raw_results: list[dict] = []
        seen_urls: set[str] = set()

        for sq in search_queries:
            try:
                items = await asyncio.to_thread(_brave_search, sq)
            except Exception as e:
                print(f"[research_count] search failed q={sq!r}: {type(e).__name__}: {e}", flush=True)
                continue

            for x in items:
                url = (x.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw_results.append({
                    "search_query": sq,
                    "title": (x.get("title") or "").strip(),
                    "url": url,
                    "description": (x.get("description") or "").strip(),
                })

        if not raw_results:
            await message.answer("Ничего пригодного не нашла. Пустая выдача — тоже ответ, но мерзкий.")
            return

        def _fetch_page_text(url: str) -> str:
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                if not r.ok:
                    return ""
                text = r.text or ""
                text = re.sub(r"(?is)<script.*?</script>", " ", text)
                text = re.sub(r"(?is)<style.*?</style>", " ", text)
                text = re.sub(r"(?is)<[^>]+>", " ", text)
                text = html.unescape(text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:5000]
            except Exception:
                return ""

        enriched: list[dict] = []
        for x in raw_results[:max_pages]:
            page_text = await asyncio.to_thread(_fetch_page_text, x["url"])
            y = dict(x)
            y["page_text"] = page_text
            enriched.append(y)

        extract_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты извлекаешь счетные факты из поисковых результатов и текстов страниц.\n"
                        "Опирайся только на переданные данные.\n"
                        "Не придумывай факты.\n"
                        "Если факт не подтверждён текстом — не включай его.\n"
                        "Верни только JSON.\n\n"
                        "JSON:\n"
                        "{"
                        "\"records\":["
                        "{"
                        "\"date\":\"YYYY-MM-DD или пусто\","
                        "\"location\":\"место или пусто\","
                        "\"event\":\"кратко что произошло\","
                        "\"metric_value\":число,"
                        "\"metric_label\":\"что посчитано\","
                        "\"source_type\":\"official|media_with_official_reference|media|unknown\","
                        "\"source_url\":\"url\","
                        "\"evidence\":\"короткая цитата/пересказ опоры\","
                        "\"confidence\":\"high|medium|low\""
                        "}"
                        "]"
                        "}\n\n"
                        "Правила:\n"
                        "- Один record = один счетный инцидент/факт.\n"
                        "- Не суммируй сам на этом этапе.\n"
                        "- Если одно событие встретилось в нескольких источниках, можно вернуть несколько records; дедупликация будет позже.\n"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Исходный запрос пользователя:\n{query}\n\n"
                        f"План поиска:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                        f"Материалы:\n{json.dumps(enriched, ensure_ascii=False)[:55000]}"
                    ),
                },
            ],
        )

        extract_text = (getattr(extract_resp, "output_text", "") or "").strip()
        try:
            extracted = json.loads(re.search(r"\{.*\}", extract_text, flags=re.DOTALL).group(0))
        except Exception:
            extracted = {"records": []}

        records = extracted.get("records") if isinstance(extracted.get("records"), list) else []

        source_policy = str(plan.get("source_policy") or "").strip().lower()
        official_only = source_policy == "official_only"

        incident_aggregate = _research_aggregate_records(
            records,
            official_only=official_only,
        )

        incident_text = _research_format_result(
            query,
            incident_aggregate,
            source_policy=source_policy,
        )

        aggregate_claims = []

        try:
            aggregate_extract_resp = client.responses.create(
                model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Ты извлекаешь агрегированные статистические оценки.\n"
                            "Нужны именно уже опубликованные ИТОГОВЫЕ цифры.\n"
                            "Не список инцидентов.\n"
                            "Опирайся только на переданные материалы.\n"
                            "Не придумывай цифры.\n"
                            "Верни только JSON.\n\n"
                            "{"
                            "\"claims\":["
                            "{"
                            "\"value\":число,"
                            "\"value_text\":\"как указано\","
                            "\"metric\":\"что считается\","
                            "\"period\":\"период\","
                            "\"source_name\":\"источник\","
                            "\"source_type\":\"official|international_org|major_media|expert_estimate|media|unknown\","
                            "\"source_url\":\"url\","
                            "\"evidence\":\"основание\","
                            "\"confidence\":\"high|medium|low\""
                            "}"
                            "]"
                            "}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Запрос пользователя:\n{query}\n\n"
                            f"Материалы:\n{json.dumps(enriched, ensure_ascii=False)[:65000]}"
                        ),
                    },
                ],
            )

            aggregate_extract_text = (
                getattr(aggregate_extract_resp, "output_text", "") or ""
            ).strip()

            try:
                aggregate_extracted = json.loads(
                    re.search(
                        r"\{.*\}",
                        aggregate_extract_text,
                        flags=re.DOTALL,
                    ).group(0)
                )
            except Exception:
                aggregate_extracted = {"claims": []}

            aggregate_claims = (
                aggregate_extracted.get("claims")
                if isinstance(aggregate_extracted.get("claims"), list)
                else []
            )

        except Exception as e:
            print(
                f"[research_count] aggregate extraction failed: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )

        aggregate_text = _aggregate_format_claims(
            query,
            aggregate_claims,
        )

        final_text = (
            "=== ГОТОВЫЕ ОЦЕНКИ ИЗ ИСТОЧНИКОВ ===\n\n"
            + aggregate_text.strip()
            + "\n\n"
            + "=== СУММА НАЙДЕННЫХ ОТДЕЛЬНЫХ СООБЩЕНИЙ ===\n\n"
            + incident_text.strip()
        )

        _remember_topic(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            {
                "type": "research_count",
                "query": query,
                "summary": final_text[:1200],
                "search_queries": search_queries,
                "aggregate": incident_aggregate,
                "aggregate_claims": aggregate_claims[:20],
            },
        )

        try:
            final_text = chatgpt_dialog.polish_research_reply(
                int(message.chat.id),
                int(message.from_user.id) if message.from_user else 0,
                query,
                final_text,
            )
        except Exception:
            pass

        await _answer_long(message, final_text)

    except Exception as e:
        print(f"[research_count] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"сложный поиск сломался: {type(e).__name__}: {e}")

async def _run_research_aggregate_for_message(message: Message, query: str) -> None:
    """
    Universal aggregate-statistics research executor.
    Separate from incident counting:
    - searches for already published totals/estimates;
    - extracts aggregate claims;
    - ranks sources;
    - returns estimate/range with source disagreement.
    """
    query = (query or "").strip()
    query = re.sub(r"\s+", " ", query).strip()
    query = query[:500]

    if not query:
        await message.answer("Считать нечего. Пустой запрос — плохая статистика.")
        return

    if not BRAVE_SEARCH_API_KEY:
        await message.answer("Агрегированный поиск не подключён: нет BRAVE_SEARCH_API_KEY.")
        return

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        await message.answer("Агрегированный поиск не подключён: нет OPENAI_API_KEY.")
        return

    try:
        import requests
        from openai import OpenAI

        max_queries = int(os.getenv("V_RESEARCH_AGG_QUERIES", "6"))
        max_results_per_query = int(os.getenv("V_RESEARCH_AGG_RESULTS_PER_QUERY", "10"))
        max_pages = int(os.getenv("V_RESEARCH_AGG_FETCH_PAGES", "12"))

        client = OpenAI()

        plan_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты планировщик поиска агрегированной статистики.\n"
                        "Нужно найти уже опубликованные общие цифры/оценки, а НЕ список отдельных инцидентов.\n"
                        "Не отвечай пользователю.\n"
                        "Верни только JSON.\n\n"
                        "JSON:\n"
                        "{"
                        "\"objective\":\"что нужно узнать\","
                        "\"metric\":\"какой показатель ищем\","
                        "\"scope\":\"география/группа\","
                        "\"period\":\"период\","
                        "\"source_policy\":\"official_only|open_sources\","
                        "\"queries\":[\"query1\",\"query2\"]"
                        "}\n\n"
                        "Правила:\n"
                        "- Делай запросы под общие итоги, статистику, оценки, reports, official data.\n"
                        "- Если пользователь просит официально — source_policy=official_only.\n"
                        "- Не планируй сбор отдельных случаев.\n"
                        "- Максимум 6 queries.\n"
                    ),
                },
                {"role": "user", "content": query},
            ],
        )

        plan_text = (getattr(plan_resp, "output_text", "") or "").strip()
        try:
            plan = json.loads(re.search(r"\{.*\}", plan_text, flags=re.DOTALL).group(0))
        except Exception:
            plan = {}

        search_queries = plan.get("queries") if isinstance(plan.get("queries"), list) else []
        search_queries = [str(x).strip() for x in search_queries if str(x).strip()]
        if not search_queries:
            search_queries = [query]

        search_queries = search_queries[:max_queries]

        def _brave_search(q: str) -> list[dict]:
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                },
                params={
                    "q": q,
                    "count": max_results_per_query,
                    "search_lang": "ru",
                    "country": "RU",
                },
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            return (data.get("web") or {}).get("results") or []

        raw_results: list[dict] = []
        seen_urls: set[str] = set()

        for sq in search_queries:
            try:
                items = await asyncio.to_thread(_brave_search, sq)
            except Exception as e:
                print(f"[research_aggregate] search failed q={sq!r}: {type(e).__name__}: {e}", flush=True)
                continue

            for x in items:
                url = (x.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw_results.append({
                    "search_query": sq,
                    "title": (x.get("title") or "").strip(),
                    "url": url,
                    "description": (x.get("description") or "").strip(),
                })

        if not raw_results:
            await message.answer("Ничего пригодного не нашла. Статистика спряталась, трусливо.")
            return

        def _fetch_page_text(url: str) -> str:
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                if not r.ok:
                    return ""
                text = r.text or ""
                text = re.sub(r"(?is)<script.*?</script>", " ", text)
                text = re.sub(r"(?is)<style.*?</style>", " ", text)
                text = re.sub(r"(?is)<[^>]+>", " ", text)
                text = html.unescape(text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:6000]
            except Exception:
                return ""

        enriched: list[dict] = []
        for x in raw_results[:max_pages]:
            page_text = await asyncio.to_thread(_fetch_page_text, x["url"])
            y = dict(x)
            y["page_text"] = page_text
            enriched.append(y)

        extract_resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты извлекаешь агрегированные статистические утверждения из материалов.\n"
                        "Это НЕ инциденты и НЕ список событий.\n"
                        "Опирайся только на переданные материалы.\n"
                        "Не придумывай цифры.\n"
                        "Если цифра не является общей оценкой/итогом по запросу — не включай.\n"
                        "Верни только JSON.\n\n"
                        "JSON:\n"
                        "{"
                        "\"claims\":["
                        "{"
                        "\"value\":число,"
                        "\"value_text\":\"как цифра написана в источнике\","
                        "\"metric\":\"что измеряется\","
                        "\"scope\":\"география/группа\","
                        "\"period\":\"период\","
                        "\"source_name\":\"название источника\","
                        "\"source_type\":\"official|international_org|major_media|expert_estimate|media|telegram|unknown\","
                        "\"source_url\":\"url\","
                        "\"evidence\":\"короткая опора из материала\","
                        "\"confidence\":\"high|medium|low\""
                        "}"
                        "]"
                        "}\n\n"
                        "Правила:\n"
                        "- Не суммируй claims.\n"
                        "- Не создавай claim, если в тексте нет числового значения.\n"
                        "- Для диапазона верни два claims: нижняя и верхняя оценка, если обе явно есть.\n"
                        "- Если пользователь просит официально, предпочитай official, но не выдумывай official.\n"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Исходный запрос пользователя:\n{query}\n\n"
                        f"План поиска:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                        f"Материалы:\n{json.dumps(enriched, ensure_ascii=False)[:65000]}"
                    ),
                },
            ],
        )

        extract_text = (getattr(extract_resp, "output_text", "") or "").strip()
        try:
            extracted = json.loads(re.search(r"\{.*\}", extract_text, flags=re.DOTALL).group(0))
        except Exception:
            extracted = {"claims": []}

        claims = extracted.get("claims") if isinstance(extracted.get("claims"), list) else []
        final_text = _aggregate_format_claims(query, claims)

        _remember_topic(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            {
                "type": "research_aggregate",
                "query": query,
                "summary": final_text[:1200],
                "search_queries": search_queries,
                "claims": claims[:20],
            },
        )

        await _answer_long(message, final_text)

    except Exception as e:
        print(f"[research_aggregate] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"агрегированный поиск сломался: {type(e).__name__}: {e}")

async def _run_news_for_message(message: Message, *, hours: int, limit: int) -> None:
    try:
        async with TG_LOCK:
            print(f"[news] start hours={hours} limit={limit} sources={NEWS_SOURCES}", flush=True)

            items = await news_digest.get_news_digest(
                news_sources_path=NEWS_SOURCES,
                hours=hours,
                limit=limit,
            )

            print(f"[news] digest_items={len(items)}", flush=True)

            text = news_digest.build_html_message(items, hours=hours)

            # Telegram limit ~4096 chars; keep safe margin
            if len(text) > 3800:
                # уменьшаем количество пунктов, пока не влезет
                shrink = list(items)
                while shrink and len(text) > 3800:
                    shrink = shrink[:-1]
                    text = news_digest.build_html_message(shrink, hours=hours)
                items = shrink

            try:
                await message.answer(text, parse_mode="html")
            except Exception as e:
                # fallback: plain text (на случай битого html)
                print(f"[news] send html failed: {type(e).__name__}: {e}", flush=True)
                plain = text.replace("<b>", "").replace("</b>", "").replace("<blockquote>", "").replace("</blockquote>", "")
                await message.answer(plain)

            try:
                news_digest.mark_digest_as_seen(items)
            except Exception as e:
                print(f"[news] mark_seen failed: {type(e).__name__}: {e}", flush=True)

    except Exception as e:
        # Главное: больше не молчим
        print(f"[news] FAILED: {type(e).__name__}: {e}", flush=True)
        await message.answer("Новости сейчас не отдались (ошибка). Смотри логи [news].")


# =========================
# COMMANDS
# =========================
@dp.message(Command("news"))
async def cmd_news(message: Message) -> None:
    if not _chat_allowed(message):
        return
    await message.answer("ок. сейчас соберу сводку.")
    await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)

async def cmd_get12(message: Message) -> None:
    if not _chat_allowed(message):
        return
    user_id = int(message.chat.id)
    await _send_content(message, user_id=user_id, ingest_hours_n=None)

# =========================
# MAIN ROUTER
# =========================
async def _send_content(message: Message, *, user_id: int, ingest_hours_n: int | None, send_mode: str = "get12") -> None:
    chat_id = int(message.chat.id)

    # --- Telethon ingest (optional) ---
    if ingest_hours_n is not None:
        async with TG_LOCK:
            try:
                await ingest_hours(int(ingest_hours_n))
            except Exception as e:
                print(f"[content] ingest_hours({ingest_hours_n}) error: {e}", flush=True)

    # --- feedback keyboard ---
    def fb_kb(item_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
            InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
            InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
        ]])

    # --- videos (consume from 24h pool) ---
    sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
    sentv = _load_sent(sentv_path)

    pool_path = _pool_path("video", user_id)
    pool = _load_json(pool_path, {"ts": 0, "items": []})
    items0 = list(pool.get("items") or [])
    if (not _pool_is_fresh(pool)) or (not items0) or (not any(Path((x.get("abs_path") or "")).exists() for x in items0)):
        pool = _refresh_video_pool(user_id)

    items = list(pool.get("items") or [])
    SEND_V = 4 if send_mode in ("get12", "get24") else 2

    SEND_K = 8 if send_mode == "get24" else 8
    picked = []
    used_src_v: set[str] = set()
    used_embs: list[list[float]] = []

    def _cos(a: list[float], b: list[float]) -> float:
        # cosine similarity
        import math
        if not a or not b or len(a) != len(b):
            return -1.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(len(a)):
            x = float(a[i]); y = float(b[i])
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0 or nb <= 0:
            return -1.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    def _get_clip_emb(abs_path: str) -> list[float] | None:
        try:
            mp = Path(abs_path + ".meta.json")
            if not mp.exists():
                return None
            j = json.loads(mp.read_text(encoding="utf-8"))
            emb = j.get("clip_emb")
            if isinstance(emb, list) and emb:
                return [float(v) for v in emb]
        except Exception:
            return None
        return None

    for x in items:
        if len(picked) >= SEND_V:
            break
        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if (not item_id) or (item_id in sentv):
            continue
        if _is_banned(item_id):
            continue
        if abs_path and (not Path(abs_path).exists()):
            continue
        # diversity: не повторять источник в одной рассылке
        src = (x.get("src") or "").strip()
        if src and src in used_src_v:
            continue

        # CLIP embedding (best-effort): пишет clip_emb в meta.json если может
        # если deps (torch/open_clip) не стоят — просто вернёт False и идём дальше без дедупа
        if abs_path:
            try:
                clip_embedder.ensure_meta_clip_emb(abs_path)
            except Exception:
                pass

        emb = _get_clip_emb(abs_path) if abs_path else None
        if emb:
            # дедуп по визуальной похожести
            too_similar = False
            for e2 in used_embs:
                if _cos(emb, e2) >= float(os.getenv("V_VIDEO_CLIP_SIM_THR", "0.88")):
                    too_similar = True
                    break
            if too_similar:
                continue
        picked.append(x)

        if src:
            used_src_v.add(src)
        if emb:
            used_embs.append(emb)

    actually_sent_ids = set()

    for x in picked:
        item_id = x.get("item_id") or ""
        abs_path = x.get("abs_path") or ""
        tmp_path = f"/tmp/vesya_video_{uuid.uuid4().hex}.mp4"

        if not item_id or not abs_path:
            continue

        try:
            if os.path.getsize(abs_path) > MAX_UPLOAD_BYTES:
                print(f"[send] skip too large video: {abs_path} size={os.path.getsize(abs_path)}", flush=True)
                continue
        except Exception as e:
            print(f"[send] size check failed video: {abs_path}: {e}", flush=True)
            continue

        try:
            shutil.copyfile(abs_path, tmp_path)
            try:
                await message.answer_video(
                    FSInputFile(tmp_path),
                    reply_markup=fb_kb(item_id),
                    request_timeout=int(os.getenv("V_VIDEO_SEND_TIMEOUT", "45")),
                )
                sentv.add(item_id)
                from datetime import datetime, timezone

                POSTED_TSV = Path("/data/a_posted_master.tsv")

                try:
                    ts = datetime.now(timezone.utc).isoformat()
                    with POSTED_TSV.open("a", encoding="utf-8") as f:
                        f.write(f"{ts}\t{user_id}\t{item_id}\tfeed_a_video\n")
                except Exception:
                    pass
                actually_sent_ids.add(item_id)
            except Exception as e:
                print(f"[send][video] FAILED item_id={item_id} path={abs_path}: {type(e).__name__}: {e}", flush=True)
                continue
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    # remove sent from pool + save
    pool["items"] = [x for x in items if (x.get("item_id") not in actually_sent_ids)]
    _save_json(pool_path, pool)

    _save_sent(sentv_path, sentv, keep_last=700)

    sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
    sentv = _load_sent(sentv_path)


    # --- мемы (consume from 24h pool + GPT batch ranking) ---
    sentm_path = DATA_DIR / f"sent_meme_{user_id}.json"
    sentm = _load_sent(sentm_path)

    pool_path = _pool_path("meme", user_id)
    pool = _load_json(pool_path, {"ts": 0, "items": []})
    items0 = list(pool.get("items") or [])
    if (not _pool_is_fresh(pool)) or (not items0) or (not any(Path((x.get("abs_path") or "")).exists() for x in items0)):
        pool = _refresh_meme_pool(user_id)

    items = list(pool.get("items") or [])

    POOL_N = int(os.getenv("V_MEME_POOL_N", "30"))   # сколько показать GPT за раз
    SEND_K = 8 if send_mode in ("get12", "get24") else 4
    def _size_ok(x: dict) -> bool:
        abs_path = (x.get("abs_path") or "").strip()
        if not abs_path:
            return False
        try:
            return os.path.getsize(abs_path) <= MAX_UPLOAD_BYTES
        except Exception:
            return False

    def _mk_batch(cand_items: list[dict]) -> list[chatgpt_dialog.MemeCandidate]:
        out: list[chatgpt_dialog.MemeCandidate] = []
        for x in cand_items:
            try:
                p = Path(x.get("abs_path") or "")
                if not p.exists():
                    continue
                suf = p.suffix.lower()
                if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                if p.stat().st_size < 5000:
                    continue
                img_bytes = p.read_bytes()
                out.append(
                    chatgpt_dialog.MemeCandidate(
                        item_id=(x.get("item_id") or "").strip(),
                        img_bytes=img_bytes,
                        caption=(x.get("caption") or "").strip(),
                        src=(x.get("src") or "").strip(),
                    )
                )
            except Exception:
                continue
        return out

    cand = []
    for x in items:
        if len(cand) >= POOL_N:
            break
        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if (not item_id) or (item_id in sentm):
            continue
        if _is_banned(item_id):
            continue
        if abs_path and (not Path(abs_path).exists()):
            continue

        # MEME candidates must be images only (skip mp4 etc.)
        if abs_path:
            suf = Path(abs_path).suffix.lower()
            if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue

        # hard pre-filter before GPT ranking (NSFW / personal / ads / trash)
        if abs_path:
            try:
                src = (x.get("src") or "").strip()
                ok = await _gpt_meme_ok(abs_path, src=src)
                if not ok:
                    continue
            except Exception:
                continue

        cand.append(x)
    print(f"[meme_pool] cand={len(cand)} pool_items={len(items)}", flush=True)

    batch: list[chatgpt_dialog.MemeCandidate] = []
    for x in cand:
        try:
            p = Path(x.get("abs_path") or "")

            if (not p.exists()):
                continue

            suf = p.suffix.lower()
            if suf not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue

            # защита от mp4 под видом jpg
            if p.stat().st_size < 5000:
                continue

            img_bytes = p.read_bytes()

            batch.append(
                chatgpt_dialog.MemeCandidate(
                    item_id=x.get("item_id") or "",
                    img_bytes=img_bytes,
                    caption=(x.get("caption") or "").strip(),
                    src=(x.get("src") or "").strip(),
                )
            )
        except Exception:
            pass

    # -------------------------
    # GPT RANK WITH GPT FILL-UP (NO NON-GPT FALLBACK)
    # -------------------------

    # GPT should not waste picks on too-large files -> prefilter by size for ranking pool
    cand_rankable = [x for x in cand if _size_ok(x)]
    print(f"[meme_pool] rankable={len(cand_rankable)} (size_ok) out of cand={len(cand)}", flush=True)

    picked_ids: list[str] = []
    picked_set: set[str] = set()

    # -------------------------
    # CASCADE GPT BATCHES (18 → next 18 → ...)
    # -------------------------

    batch_size = 18
    picked_ids: list[str] = []
    picked_set: set[str] = set()

    start = 0
    total = len(cand_rankable)

    PICK_BUFFER = int(os.getenv("V_MEME_PICK_BUFFER", "6"))  # запас на скипы при отправке
    target_pick = SEND_K + PICK_BUFFER

    # map for send phase
    id2 = {
        (x.get("item_id") or "").strip(): x
        for x in cand_rankable
        if (x.get("item_id") or "").strip()
    }

    while start < total and len(picked_ids) < target_pick:
        slice_items = cand_rankable[start:start + batch_size]

        if not slice_items:
            break

        batch = _mk_batch(slice_items)
        if not batch:
            start += batch_size
            continue

        need = min(batch_size, target_pick - len(picked_ids))

        r = chatgpt_dialog.meme_rank_batch(batch, top_k=need)
        new_ids = list((r or {}).get("picked_item_ids") or [])

        # очистка
        new_ids = [
            pid for pid in new_ids
            if pid and pid not in picked_set and pid not in sentm
        ]

        for pid in new_ids:
            picked_ids.append(pid)
            picked_set.add(pid)

        print(
            f"[MEME_GPT_CASCADE] slice={start}-{start+batch_size} "
            f"picked_now={len(new_ids)} total={len(picked_ids)}",
            flush=True
        )

        start += batch_size

    # 3) send up to SEND_K реально отправленных
    actually_sent_ids: set[str] = set()
    sent_count = 0

    used_src: set[str] = set()

    for pid in picked_ids:
        if sent_count >= SEND_K:
            break

        x = id2.get(pid)
        if not x:
            continue
        src = (x.get("src") or "").strip()
        if src and src in used_src:
            continue

        item_id = (x.get("item_id") or "").strip()
        abs_path = (x.get("abs_path") or "").strip()
        if not item_id or item_id in sentm:
            continue

        # (size already ok in cand_rankable, but keep extra safety)
        try:
            if os.path.getsize(abs_path) > MAX_UPLOAD_BYTES:
                print(f"[send] skip too large meme (post-rank): {abs_path} size={os.path.getsize(abs_path)}", flush=True)
                continue
        except Exception as e:
            print(f"[send] size check failed meme: {abs_path}: {e}", flush=True)
            continue

        try:
            await message.answer_photo(
                FSInputFile(abs_path),
                reply_markup=fb_kb(item_id),
            )
        except Exception as e:
            print(
                f"[send][meme] FAILED item_id={item_id} path={abs_path}: {type(e).__name__}: {e}",
                flush=True,
            )
            continue

        sentm.add(item_id)

        from datetime import datetime, timezone

        POSTED_TSV = Path("/data/a_posted_master.tsv")

        try:
            ts = datetime.now(timezone.utc).isoformat()
            with POSTED_TSV.open("a", encoding="utf-8") as f:
                f.write(f"{ts}\t{user_id}\t{item_id}\tfeed_memes\n")
        except Exception:
            pass

        actually_sent_ids.add(item_id)
        sent_count += 1

        if src:
            used_src.add(src)

    print(f"[MEME_SEND] want={SEND_K} sent={sent_count} picked_total={len(picked_ids)} cand={len(cand)} rankable={len(cand_rankable)}", flush=True)

    # 4) remove from pool ONLY реально отправленные
    pool["items"] = [x for x in items if (x.get("item_id") not in actually_sent_ids)]
    _save_json(pool_path, pool)

    _save_sent(sentm_path, sentm, keep_last=700)

    # --- youtube links ---
    try:
        def yt_kb(item_id: str) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
                InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
                InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
            ]])

        sentyt_path = DATA_DIR / f"sent_yt_{user_id}.json"
        sentyt = _load_sent(sentyt_path)
        SEND_YT = 2 if send_mode == "get24" else 1
        yt_sent = 0

        ytstate_path = DATA_DIR / f"yt_state_{user_id}.json"
        try:
            _st = json.loads(ytstate_path.read_text(encoding="utf-8")) if ytstate_path.exists() else {}
        except Exception:
            _st = {}

        posted_ids = set(_st.get("posted_video_ids") or [])
        last_sent_by_source = dict(_st.get("last_sent_by_source") or {})
        last_sent_by_channel = dict(_st.get("last_sent_by_channel") or {})
        now_ts = int(time.time())
        CHANNEL_COOLDOWN_SEC = int(os.getenv("V_YT_CHANNEL_COOLDOWN_DAYS", "7")) * 86400

        pool = c_youtube_fetcher.get_batch(
            limit=6,
            posted_video_ids=posted_ids,
            last_sent_by_channel=last_sent_by_channel,
            channel_cooldown_sec=CHANNEL_COOLDOWN_SEC,
            mode="mix",
        )

        print(f"[yt] pool size={len(pool)}", flush=True)

        if not pool:
            yt_alert_path = DATA_DIR / "yt_master_exhausted_alert.json"
            yt_alert_cooldown_sec = int(os.getenv("YT_EMPTY_ALERT_HOURS", "12")) * 3600

            last_alert_ts = 0
            try:
                if yt_alert_path.exists():
                    last_alert_ts = int(json.loads(yt_alert_path.read_text(encoding="utf-8")).get("ts") or 0)
            except Exception:
                last_alert_ts = 0

            if now_ts - last_alert_ts >= yt_alert_cooldown_sec:
                sent_any = False

                for admin_user_id in ADMIN_USER_IDS:
                    try:
                        await bot.send_message(
                            chat_id=admin_user_id,
                            text=(
                                "⚠️ YouTube-база исчерпана.\n"
                                "Текущий master/work pool больше не дает роликов на выдачу.\n"
                                "Пора вручную собирать новый пул ссылок."
                            ),
                        )
                        sent_any = True
                    except Exception:
                        pass

                if sent_any:
                    try:
                        yt_alert_path.write_text(json.dumps({"ts": now_ts}), encoding="utf-8")
                    except Exception:
                        pass

        for x in pool:
            title = (x.get("title") or "").strip()
            url = (x.get("url") or "").strip()
            vid = (x.get("video_id") or "").strip()

            if not url or url in sentyt:
                continue

            item_id = f"yt:{url}"

            if _is_banned(item_id):
                continue

            text2 = f"🎵 {title}\n{url}" if title else url

            cid = (x.get("channel_id") or "").strip()
            if not cid:
                continue
            if cid:
                last_ts = int(last_sent_by_channel.get(cid) or 0)
                if now_ts - last_ts < CHANNEL_COOLDOWN_SEC:
                    continue

            await message.answer(text2, reply_markup=yt_kb(item_id))

            # фиксируем отправку СРАЗУ (иначе при SEND_YT=1 break съедает запись state)
            sentyt.add(url)

            if vid:
                posted_ids.add(vid)
                last_sent_by_source["c_youtube"] = vid

            if cid:
                last_sent_by_channel[cid] = now_ts

            yt_sent += 1
            if yt_sent >= SEND_YT:
                break

        _save_sent(sentyt_path, sentyt, keep_last=800)

        ytstate_path.write_text(
             json.dumps({
                 "posted_video_ids": list(posted_ids)[-5000:],
                 "last_sent_by_source": last_sent_by_source,
                 "last_sent_by_channel": last_sent_by_channel,
             }),

            encoding="utf-8",
        )

    except Exception as e:
       print(f"[content] youtube error: {e}", flush=True)

@dp.message(F.photo)
async def on_photo(message: Message) -> None:
    print("[IMG] photo handler triggered", flush=True)

    if _relay_is_armed(message):
        _relay_disarm(message)

        if not _is_admin_user(message):
            return

        ok = await _copy_to_main_group(message)
        if ok:
            await message.answer("кинула.")
        else:
            await message.answer("не вышло.")
        return

    if not _chat_allowed(message):
        return

    try:
        ph = message.photo[-1]
        # save last photo id even if limiter skips reactions
        uid = int(message.from_user.id) if message.from_user else 0
        LAST_USER_IMAGE_ID[(int(message.chat.id), uid)] = ph.file_id

        raw = await _download_tg_file_bytes(message.bot, ph.file_id)
        img_bytes = _shrink_jpeg_bytes(raw)

        caption = (message.caption or "").strip()

        if not _group_message_addresses_vesya(message, caption):
            return

        if await handle_analytics_photo(message, img_bytes, _answer_long):
            return

        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )

        if await _try_universal_message_layer(message, caption, event_type="photo"):
            return

        # Пересланные фото/новости в группе сами по себе не комментируем.
        # Иначе Веся отвечает дважды: на forward-картинку и на реплику пользователя.
        if (
            message.chat.type in ("group", "supergroup")
            and _is_forwarded_message(message)
            and not (caption and chatgpt_dialog.persona.is_addressed(caption))
        ):
            return

        if caption:
            if any(x in caption.lower() for x in (
                "прочитай",
                "текст",
                "документ",
                "скрин",
                "ocr",
                "что написано",
                "разбери документ",
            )):
                extracted = await asyncio.to_thread(_ocr_image_bytes, img_bytes, filename="photo.jpg")
                dd = chatgpt_dialog.analyze_document_text(caption, "photo.jpg", extracted)
            else:
                dd = chatgpt_dialog.describe_or_compare_photo(caption, img_bytes)
            reply = ((dd.reply if dd else "") or "").strip()

            if not reply:
                reply = "Посмотрела. Визуальный аргумент засчитан, смысл — по остаточному принципу."

            _remember_topic(
                int(message.chat.id),
                int(message.from_user.id) if message.from_user else 0,
                {
                    "type": "photo",
                    "user_prompt": caption,
                    "summary": reply,
                },
            )

            await message.answer(reply)
            return
        fn = IMG_INBOX / f"{message.chat.id}_{message.message_id}.jpg"

        try:
            fn.write_bytes(img_bytes)
        except Exception:
            pass
        # In groups: react rarely (cooldown + probability)
        if message.chat.type in ("group", "supergroup"):
            # разрешаем прогноз для групп так же, как для лички
            run_forecast()  # использовать is_forecast_query и forecast_resp
        else:
            run_forecast()
        res = chatgpt_dialog.image_react(
            chat_id=int(message.chat.id),
            user_id=int(message.from_user.id) if message.from_user else 0,
            caption=(message.caption or ""),
            img_bytes=img_bytes,
        )

        if not res:
            return

        action = (res.get("action") or "skip").lower()

        reply = (res.get("reply") or "").strip()
        kind = (res.get("kind") or "photo").lower()
        # In groups: for non-meme photos do only "like" (no comments)
        if message.chat.type in ("group", "supergroup") and kind != "meme":
            if action == "comment":
                action = "like"
        if kind == "meme" and action != "skip" and (not _img_should_react(int(message.chat.id))):
            print("[IMG] meme skipped by limiter", flush=True)
            return

        if action == "like":
            await message.reply(reply or "🔥")

        elif action == "comment":
            await message.reply(reply or "норм")

    except Exception as e:
        print(f"[img] photo error: {e}", flush=True)
@dp.message(F.document)
async def on_document(message: Message) -> None:
    if not _chat_allowed(message):
        return

    if _relay_is_armed(message):
        _relay_disarm(message)

        if not _is_admin_user(message):
            return

        ok = await _copy_to_main_group(message)
        if ok:
            await message.answer("кинула.")
        else:
            await message.answer("не вышло.")
        return

    doc = message.document
    if not doc:
        return

    mt = (getattr(doc, "mime_type", "") or "").lower()
    fn = getattr(doc, "file_name", "") or "document"
    caption = (message.caption or "").strip()

    if not _group_message_addresses_vesya(message, caption):
        return

    if await _try_universal_message_layer(message, caption, event_type="document"):
        return

    # =========================
    # IMAGE DOCUMENTS
    # =========================
    if mt.startswith("image/"):
        try:
            raw = await _download_tg_file_bytes(message.bot, doc.file_id)
            img_bytes = _shrink_jpeg_bytes(raw)

            uid = int(message.from_user.id) if message.from_user else 0
            LAST_USER_IMAGE_ID[(int(message.chat.id), uid)] = doc.file_id

            chatgpt_dialog.note_last_user_photo(
                int(message.chat.id),
                int(message.from_user.id) if message.from_user else 0,
                img_bytes,
            )

            if caption and (
                chatgpt_dialog.persona.is_addressed(caption)
                or (message.chat.type == "private" and _wants_context_comment(caption))
            ):
                if (
                    any(x in caption.lower() for x in (
                        "прочитай",
                        "текст",
                        "документ",
                        "скрин",
                        "ocr",
                        "что написано",
                        "разбери документ",
                    ))
                    or _looks_like_text_heavy_image(img_bytes)
                ):
                    extracted = await asyncio.to_thread(_ocr_image_bytes, img_bytes, filename=fn)
                    dd = chatgpt_dialog.analyze_document_text(caption, fn, extracted)
                else:
                    dd = chatgpt_dialog.describe_or_compare_photo(
                        f"Веся, прокомментируй это в своём вкусе. Вопрос пользователя: {caption}",
                        img_bytes,
                    )
                if dd and (dd.reply or "").strip():
                    await _answer_long(message, dd.reply)
                    return

                if not _group_message_addresses_vesya(message, caption):
                    return

            res = chatgpt_dialog.image_react(
                chat_id=int(message.chat.id),
                user_id=int(message.from_user.id) if message.from_user else 0,
                caption=(message.caption or ""),
                img_bytes=img_bytes,
            )

            if not res:
                return

            action = (res.get("action") or "skip").lower()
            reply = (res.get("reply") or "").strip()
            kind = (res.get("kind") or "photo").lower()

            if message.chat.type in ("group", "supergroup") and kind != "meme":
                if action == "comment":
                    action = "like"

            if kind == "meme" and action != "skip" and (not _img_should_react(int(message.chat.id))):
                print("[IMG] meme skipped by limiter", flush=True)
                return

            if action == "like":
                await message.reply(reply or "👍")
            elif action == "comment":
                await message.reply(reply or "ок")

        except Exception as e:
            print(f"[img] doc error: {e}", flush=True)

        return

    # Existing direct video files as documents are not analyzed here.
    if mt.startswith("video/"):
        return

    # =========================
    # TEXT / PDF DOCUMENTS
    # =========================
    if message.chat.type in ("group", "supergroup"):
        if not (caption and chatgpt_dialog.persona.is_addressed(caption)):
            return

    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        raw = await _download_tg_file_bytes(message.bot, doc.file_id)
        extracted = await asyncio.to_thread(_extract_text_document, raw, fn, mt)

        dd = chatgpt_dialog.analyze_document_text(
            caption or "Веся, проанализируй документ.",
            fn,
            extracted,
        )

        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id) if message.from_user else 0

        _remember_topic(chat_id, user_id, {
            "type": "document",
            "filename": fn,
            "user_prompt": caption or "",
            "summary": (dd.reply if dd else "") or "",
        })

        if dd and (dd.reply or "").strip():
            chatgpt_dialog.add_user(chat_id, user_id, caption or f"Веся, проанализируй документ {fn}")
            chatgpt_dialog.add_assistant(chat_id, user_id, dd.reply)
            await _answer_long(message, dd.reply)
        else:
            await message.answer("документ открыла, но текста не вытащила.")

    except Exception as e:
        print(f"[document] analyze error: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"документ не разобрала: {type(e).__name__}: {e}")

@dp.message(F.video)
async def on_video(message: Message) -> None:
    now = time.time()
    k = (int(message.chat.id), int(message.message_id))

    for kk, ts in list(RECENT_MSG_IDS.items()):
        if now - ts > 60:
            RECENT_MSG_IDS.pop(kk, None)

    if k in RECENT_MSG_IDS:
        print(f"[VIDEO] duplicate msg_id={message.message_id} skipped", flush=True)
        return

    RECENT_MSG_IDS[k] = now

    if not _chat_allowed(message):
        return

    if _relay_is_armed(message):
        _relay_disarm(message)

        if not _is_admin_user(message):
            return

        ok = await _copy_to_main_group(message)
        if ok:
            await message.answer("кинула.")
        else:
            await message.answer("не вышло.")
        return

    caption = (message.caption or "").strip()

    if message.chat.type in ("group", "supergroup"):
        if not (caption and chatgpt_dialog.persona.is_addressed(caption)):
            return

    try:
        raw = await _download_tg_file_bytes(message.bot, message.video.file_id)

        frames = await asyncio.to_thread(_extract_video_frames, raw, 5)
        audio_mp3 = await asyncio.to_thread(_extract_video_audio_mp3, raw)

        dd = chatgpt_dialog.describe_video_frames(
            caption or "Веся, посмотри видео и прокомментируй.",
            frames,
            audio_mp3,
        )

        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id) if message.from_user else 0

        music_track = ""
        try:
            music_track = chatgpt_dialog.recognize_music_audd(audio_mp3)
        except Exception:
            music_track = ""

        _remember_topic(chat_id, user_id, {
            "type": "video",
            "user_prompt": caption or "",
            "summary": (dd.reply if dd else "") or "",
            "music_track": music_track,
        })

        if dd and (dd.reply or "").strip():
            chat_id = int(message.chat.id)
            user_id = int(message.from_user.id) if message.from_user else 0

            chatgpt_dialog.add_user(
                chat_id,
                user_id,
                caption or "Веся, посмотри видео и прокомментируй.",
            )
            chatgpt_dialog.add_assistant(chat_id, user_id, dd.reply)

            await _answer_long(message, dd.reply)
        else:
            await message.answer("видео посмотрела. Ничего внятного не вытащила.")

    except Exception as e:
        print(f"[video] discuss error: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"видео не разобрала: {type(e).__name__}: {e}")

def _extract_manual_youtube_query(text: str) -> str | None:
    t = (text or "").strip()

    t = re.sub(
        r"^\s*(веся|веслава|веська|vesya|сергеевна)\s*[,.:;!\-]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()

    tl = t.lower()

    # thematic YouTube request:
    # "дай ролик про вечер и море", "клип под дождь", "видео про дорогу"
    if not re.search(
        r"\b(клип|клипы|видео|видос|видосы|ролик|ролики|ютуб|youtube)\b",
        tl,
        flags=re.I,
    ):
        return None

    if not re.search(
        r"\b(найди|поищи|подбери|кинь|скинь|дай|пришли|покажи)\b",
        tl,
        flags=re.I,
    ):
        return None

    # fallback raw query if no llm
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return t

    try:
        from openai import OpenAI

        client = OpenAI()

        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты строишь короткий YouTube search query.\n"
                        "Верни только строку запроса.\n"
                        "Без комментариев.\n"
                        "Без JSON.\n"
                        "Без кавычек.\n"
                        "Нужно понять, какие клипы/видео хочет пользователь.\n"
                        "Убирай мусор вроде 'найди', 'покажи', 'Веся'.\n"
                        "Сохраняй смысл и атмосферу.\n"
                        "Максимум 12 слов."
                    ),
                },
                {
                    "role": "user",
                    "content": t,
                },
            ],
        )

        q = (getattr(resp, "output_text", "") or "").strip()

        q = re.sub(r"\s+", " ", q).strip()
        q = q[:120]

        return q or t

    except Exception:
        return t
    
async def _youtube_manual_search_for_message(message: Message, query: str) -> dict | None:
    import urllib.parse
    import urllib.request

    api_key = (os.getenv("YT_API_KEY") or "").strip()
    if not api_key:
        print("[yt_manual] missing YT_API_KEY", flush=True)
        return None

    raw_query = (query or "").strip()
    if not raw_query:
        return None

    print(f"[yt_manual] raw_query={raw_query!r}", flush=True)

    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "maxResults": "10",
        "q": raw_query,
        "key": api_key,
        "order": "relevance",
        "videoDuration": "medium",
        "safeSearch": "none",
    })

    url = "https://www.googleapis.com/youtube/v3/search?" + params

    def _load():
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    data = await asyncio.to_thread(_load)
    items = data.get("items") or []
    if not items:
        return None

    sentyt_path = DATA_DIR / f"manual_yt_sent_{int(message.chat.id)}.json"
    sentyt = _load_sent(sentyt_path)

    selected = None
    for item in items:
        title0 = ((item.get("snippet") or {}).get("title") or "").lower()
        desc0 = ((item.get("snippet") or {}).get("description") or "").lower()
        vid0 = ((item.get("id") or {}).get("videoId") or "").strip()

        if not vid0:
            continue
        if vid0 in sentyt:
            continue
        if "#shorts" in title0 or "#shorts" in desc0 or "shorts" in title0:
            continue

        selected = item
        break

    if selected is None:
        selected = items[0]

    vid = ((selected.get("id") or {}).get("videoId") or "").strip()
    title = ((selected.get("snippet") or {}).get("title") or "").strip()

    if not vid:
        return None

    sentyt.add(vid)
    _save_sent(sentyt_path, sentyt, keep_last=200)

    return {
        "title": title,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }

async def _handle_normalized_text_pipeline(message: Message, text: str, *, event_type: str = "text") -> None:
    text = (text or "").strip()
    if not text:
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    if not _group_message_addresses_vesya(message, text):
        return

    addressed_body = _strip_vesya_prefix(text).strip()
    addressed_body = re.sub(r"\s+", " ", addressed_body).strip(" ?!.,:;")

    if not addressed_body:
        await message.answer("Я тут. Что нужно?")
        return

    if re.search(
        r"^(?:покажи\s+)?(?:свои\s+)?функции$|"
        r"^функции$|"
        r"^(?:покажи\s+)?что\s+ты\s+умеешь$|"
        r"^что\s+умеешь$|"
        r"^меню$|^help$|^помощь$",
        addressed_body,
        flags=re.I,
    ):
        await _answer_long(
            message,
            (
                "Что умею:\n\n"

                "1. Переводчик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи переводчика с русского на английский\n"
                "   Веся включи переводчика с английского на русский\n"
                "   Выключить:\n"
                "   Веся выключи переводчика\n\n"

                "2. Аналитик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи аналитика\n"
                "   Веся включи режим аналитика\n"
                "   Выключить:\n"
                "   Веся выключи аналитика\n"
                "   Веся отключи аналитика\n\n"

                "3. Напоминания\n"
                "   Режим включать не надо.\n"
                "   Просто напиши:\n"
                "   Веся напомни завтра в 9 утра позвонить Иванову\n"
                "   Веся напомни через 30 минут проверить духовку\n\n"

                "4. Новости\n"
                "   Режим включать и выключать не надо.\n"
                "   Просто напиши:\n"
                "   Веся новости\n\n"

                "5. YouTube / музыка / клипы\n"
                "   Просто попроси найти:\n"
                "   Веся дай что-нибудь из Offspring\n"
                "   Веся найди клип Metallica\n\n"

                "6. Фото и информация\n"
                "   Просто попроси:\n"
                "   Веся дай фото Козельска\n"
                "   Веся найди информацию по объекту\n\n"

                "7. Документы, фото и скриншоты\n"
                "   Просто пришли PDF, Word, Excel, фото или скриншот и задай вопрос.\n\n"

                "8. Обычный диалог\n"
                "   Просто пиши обычным языком.\n\n"

                "Важно:\n"
                "Постоянно включаемые публичные режимы сейчас только два: переводчик и аналитик.\n"
                "Остальное запускается отдельной командой и не требует выключения."
            ),
        )
        return

    clean_action = addressed_body.lower()
    clean_action = re.sub(r"\s+", " ", clean_action).strip(" ?!.,:;")

    if re.search(r"^(?:включи|активируй|запусти)\s+(?:режим\s+)?секретар[ьяь]$", clean_action, flags=re.I):
        _set_secretary_mode(chat_id, user_id)
        await message.answer(
            "Секретарь включён.\n\n"
            "Теперь можно работать с почтой, клиентами, актами и отчетами.\n"
            "Для выхода: Веся выключи секретаря."
        )
        return

    if re.search(r"^(?:выключи|отключи|останови|заверши)\s+(?:режим\s+)?секретар[ьяь]$", clean_action, flags=re.I):
        _clear_secretary_mode(chat_id, user_id)
        await message.answer("Секретарь выключен. Возвращаюсь в обычный режим.")
        return

    if _is_secretary_mode_active(chat_id, user_id):
        try:
            from vesya_tools.secretary.handler import handle_secretary_message

            if await handle_secretary_message(message, addressed_body):
                return

        except Exception as e:
            print(f"[secretary] failed: {type(e).__name__}: {e}", flush=True)
            await message.answer(f"Секретарь сломался: {type(e).__name__}: {e}")
            return

    mode = _get_translator_mode(chat_id, user_id)
    if mode:
        translated = await asyncio.to_thread(
            _translate_bidirectional,
            text,
            str(mode.get("lang_a") or ""),
            str(mode.get("lang_b") or ""),
        )

        if translated:
            await _answer_long(message, translated)
        else:
            await message.answer("Перевод не вышел.")
        return

    if _looks_like_calendar_view_request(text):
        await _answer_long(
            message,
            _calendar_view_text(chat_id, user_id, text),
        )
        return

    if await handle_calendar_message(message, CALENDAR_STORAGE):
        return

    if re.match(r"^напомни\b", addressed_body, flags=re.I):
        await message.answer(
            "Не поняла время напоминания. Формат: "
            "«напомни сегодня в 13:00 ...», "
            "«напомни завтра в 8 утра ...», "
            "«напомни через 5 минут ...»."
        )
        return

    await _handle_text_core(message, text, event_type=event_type)

async def _handle_text_core(message: Message, dialog_text: str, *, event_type: str = "text") -> None:
    """
    Shared semantic core for normal text and transcribed voice.
    Voice must not have its own intelligence: it becomes text and uses the same router/executors.
    """
    text = (dialog_text or "").strip()
    if not text:
        return
    
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    dialog_text = text

    # =========================
    # SIMPLE FORWARD / REPLY LOGIC
    # =========================

    if message.reply_to_message:
        r = message.reply_to_message

        replied_text = (
            r.text
            or r.caption
            or ""
        ).strip()

        clean_text = _strip_vesya_prefix(text).strip().lower().strip(" ?!.,:;")

        if replied_text and re.search(
            r"^(переведи|переведи это|переведи на русский|переведи это на русский|перевод|что значит|что означает)$",
            clean_text,
            flags=re.I,
        ):
            translated = chatgpt_dialog.translate_to_ru(replied_text)
            translated = (translated or "").strip()

            if translated:
                await _answer_long(message, translated)
            else:
                await message.answer("перевести не вышло. Роскошно, конечно.")
            return

        # пользователь отвечает командой на пересланный вопрос
        if replied_text and clean_text in {
            "ответь",
            "ответь человеку",
            "ответь нормально",
            "ответь уже",
            "прокомментируй",
            "что думаешь",
            "разбери",
        }:
            dialog_text = replied_text



    pending_research = _get_topic(chat_id, user_id)
    if pending_research and pending_research.get("type") == "research_clarify":
        original_query = str(pending_research.get("query") or "").strip()

        if original_query:
            _remember_topic(
                chat_id,
                user_id,
                {
                    "type": "research_count",
                    "query": original_query,
                },
            )
            await _run_research_count_for_message(message, original_query)
            return

    if await _try_universal_message_layer(message, dialog_text, event_type=event_type):
        return

    decision = chatgpt_dialog.decide(chat_id, user_id, dialog_text)

    if decision.intent == "youtube_search":
        try:
            found = await _youtube_manual_search_for_message(
                message,
                decision.query or dialog_text,
            )

            if not found:
                await message.answer("Не нашла.")
                return

            title = (found.get("title") or "").strip()
            url = (found.get("url") or "").strip()

            await _answer_long(message, f"{title}\n{url}".strip())
            return

        except Exception as e:
            print(f"[yt_semantic] error: {type(e).__name__}: {e}", flush=True)
            await message.answer("Ошибка поиска.")
            return

    print(f"[route][{event_type}] intent={decision.intent} reply={decision.reply!r}", flush=True)

    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()

    reply_event_type = event_type
    if event_type == "voice" and intent != "chat":
        reply_event_type = "text"

    _record_memory_event(
        message,
        text=dialog_text,
        intent=intent,
        reply=reply,
        event_type=event_type,
    )

    if intent == "end":
        chatgpt_dialog.end(chat_id, user_id)
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)
        return

    if intent == "news":
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)
        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return

    if intent == "content":
        content_query = (getattr(decision, "query", "") or "").strip()

        yt_query = _extract_manual_youtube_query(content_query)
        if yt_query:
            if reply:
                await _send_reply_for_event(message, reply, event_type=reply_event_type)

            try:
                found = await _youtube_manual_search_for_message(message, yt_query)

                if not found:
                    await message.answer("Не нашла.")
                    return

                title = (found.get("title") or "").strip()
                url = (found.get("url") or "").strip()

                await _answer_long(message, f"{title}\n{url}".strip())
                return

            except Exception as e:
                print(f"[yt_manual][content] error: {type(e).__name__}: {e}", flush=True)
                await message.answer("Ошибка поиска.")
                return

        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)

        await _send_content(message, user_id=chat_id, ingest_hours_n=None)
        return

    if intent == "research_clarify":
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)

        q = (getattr(decision, "query", "") or "").strip()
        if not q:
            q = dialog_text

        _remember_topic(
            chat_id,
            user_id,
            {
                "type": "research_clarify",
                "query": q,
                "summary": reply[:1200] if reply else "",
            },
        )
        return

    if intent == "research_aggregate":
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)

        q = (getattr(decision, "query", "") or "").strip()
        if not q:
            q = _extract_web_search_query(dialog_text)

        await _run_research_aggregate_for_message(message, q)
        return

    if intent == "research_count":
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)

        q = (getattr(decision, "query", "") or "").strip()
        if not q:
            q = _extract_web_search_query(dialog_text)

        await _run_research_count_for_message(message, q)
        return

    if intent == "web_search":
        if reply:
            await _send_reply_for_event(message, reply, event_type=reply_event_type)

        q = (getattr(decision, "query", "") or "").strip()
        if not q:
            q = _extract_web_search_query(dialog_text)

        if re.search(
            r"\b(прогноз|спрогнозируй|кто выйдет|кто победит|кто пройдет|кто пройдёт|шансы|фаворит|сценарий)\b",
            dialog_text,
            flags=re.I,
        ) and not re.search(
            r"\b(прогноз|спрогнозируй|кто выйдет|кто победит|кто пройдет|кто пройдёт|шансы|фаворит|сценарий)\b",
            q,
            flags=re.I,
        ):
            q = f"{q} {dialog_text}".strip()
            q = re.sub(r"\s+", " ", q)[:250]

        topic = _get_topic(chat_id, user_id)
        if topic and topic.get("type") == "web_search":
            dtl = dialog_text.lower()
            if any(x in dtl for x in ("они", "он ", "она ", "до этого", "раньше", "последний раз", "прогноз", "спрогноз", "кто выйдет", "кто пройдет", "кто пройдёт", "из групп", "шансы", "фаворит")):
                q = f"{q} {topic.get('query', '')}".strip()
                q = re.sub(r"\s+", " ", q)
                q = q[:250]
        await _run_web_search_for_message(message, q)
        return

    if reply:
        await _send_reply_for_event(message, reply, event_type=reply_event_type)

@dp.message(F.voice)
async def on_voice(message: Message) -> None:
    if not _chat_allowed(message):
        return

    if _relay_is_armed(message):
        return
    
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        raw = await _download_tg_file_bytes(message.bot, message.voice.file_id)
        text = await asyncio.to_thread(_transcribe_voice_ogg, raw)

        if not text:
            await message.answer("голос не разобрала.")
            return

        print(f"[voice] transcribed={text!r}", flush=True)

        key = (int(message.chat.id), int(message.message_id))
        VOICE_TEXT_MSG_IDS[key] = time.time()

        text_message = message.model_copy(update={"text": text})
        await vesya_handler(text_message)

    except Exception as e:
        print(f"[voice] handler failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"голос сломался: {type(e).__name__}: {e}")

@dp.message(F.text)
async def vesya_handler(message: Message) -> None:
    print(f"[DEBUG] msg_id={message.message_id} chat_id={message.chat.id} from={message.from_user.id if message.from_user else 0}", flush=True)

    now = time.time()
    k = (int(message.chat.id), int(message.message_id))

    for kk, ts in list(RECENT_MSG_IDS.items()):
        if now - ts > 60:
            RECENT_MSG_IDS.pop(kk, None)
    if k in RECENT_MSG_IDS:
        print(f"[DEBUG] DUPLICATE msg_id={message.message_id} skipped", flush=True)
        return
    RECENT_MSG_IDS[k] = now

    if not _chat_allowed(message):
        return

    text = (message.text or "").strip()
    orig_text = text

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    # 🔥 SECRETARY EARLY ROUTE (ВАЖНО)
    if _is_secretary_mode_active(chat_id, user_id):
        from vesya_tools.secretary.handler import handle_secretary_message

        result = await handle_secretary_message(message, text)

        if result:
            return

    # === SAVE PRIVATE USERS ===
    if message.chat.type == "private" and message.from_user:
        users = _load_private_users()
        users.add(int(message.from_user.id))
        _save_private_users(users)

    event_key = (int(message.chat.id), int(message.message_id))
    incoming_event_type = "voice" if event_key in VOICE_TEXT_MSG_IDS else "text"

    for kk, ts in list(VOICE_TEXT_MSG_IDS.items()):
        if now - ts > 60:
            VOICE_TEXT_MSG_IDS.pop(kk, None)
    print(
        "[TEXT_DEBUG] "
        f"chat_type={message.chat.type!r} "
        f"text={text!r} "
        f"forward_origin={getattr(message, 'forward_origin', None)!r} "
        f"forward_date={getattr(message, 'forward_date', None)!r} "
        f"forward_from={getattr(message, 'forward_from', None)!r} "
        f"forward_sender_name={getattr(message, 'forward_sender_name', None)!r} "
        f"forward_from_chat={getattr(message, 'forward_from_chat', None)!r} "
        f"is_forward={_is_forwarded_message(message)}",
        flush=True,
    )

    # Если это сообщение с медиа (фото/видео/документ) — НЕ обрабатываем как текст
    if message.photo or message.video or message.document:
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    if message.chat.type in ("group", "supergroup"):
        addressed_to_vesya = _group_message_addresses_vesya(message, text)

        mute_seconds = _parse_group_mute_seconds(text)
        if addressed_to_vesya and mute_seconds:
            _set_group_mute(chat_id, mute_seconds)
            await message.answer("Пауза включена. Молчу.")
            return

        if _is_group_muted(chat_id):
            if addressed_to_vesya and _is_group_unmute_command(text):
                _clear_group_mute(chat_id)
                await message.answer("Пауза снята.")
                return
            return

    # ЖЁСТКИЙ GROUP GATE:
    # в группе Веся не влезает в чужие разговоры.
    # Отвечает только на /команду, прямое обращение или reply на её сообщение.
    if message.chat.type in ("group", "supergroup"):
        if not _group_message_addresses_vesya(message, text):
            return

    addressed_body = _strip_vesya_prefix(text).strip()
    addressed_body = re.sub(r"\s+", " ", addressed_body).strip(" ?!.,:;")

    if not addressed_body:
        await message.answer("Я тут. Что нужно?")
        return

    if re.search(
        r"^(?:покажи\s+)?(?:свои\s+)?функции$|"
        r"^функции$|"
        r"^(?:покажи\s+)?что\s+(?:ты\s+)?умеешь$|"
        r"^что\s+умеешь$|"
        r"^меню$|^help$|^помощь$",
        addressed_body,
        flags=re.I,
    ):
        await _answer_long(
            message,
            (
                "Что умею:\n\n"

                "1. Переводчик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи переводчика с русского на английский\n"
                "   Веся включи переводчика с английского на русский\n"
                "   Выключить:\n"
                "   Веся выключи переводчика\n\n"

                "2. Аналитик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи аналитика\n"
                "   Веся включи режим аналитика\n"
                "   Выключить:\n"
                "   Веся выключи аналитика\n"
                "   Веся отключи аналитика\n\n"

                "3. Напоминания\n"
                "   Режим включать не надо.\n"
                "   Просто напиши:\n"
                "   Веся напомни завтра в 9 утра позвонить Иванову\n"
                "   Веся напомни через 30 минут проверить духовку\n\n"

                "4. Новости\n"
                "   Режим включать и выключать не надо.\n"
                "   Просто напиши:\n"
                "   Веся новости\n\n"

                "5. YouTube / музыка / клипы\n"
                "   Просто попроси найти:\n"
                "   Веся дай что-нибудь из Offspring\n"
                "   Веся найди клип Metallica\n\n"

                "6. Фото и информация\n"
                "   Просто попроси:\n"
                "   Веся дай фото Козельска\n"
                "   Веся найди информацию по объекту\n\n"

                "7. Документы, фото и скриншоты\n"
                "   Просто пришли PDF, Word, Excel, фото или скриншот и задай вопрос.\n"
                "   Например:\n"
                "   Веся, что в этом документе главное?\n"
                "   Веся, проверь этот скриншот\n\n"

                "8. Обычный диалог\n"
                "   Просто пиши обычным языком.\n\n"

                "Важно:\n"
                "Постоянно включаемые публичные режимы сейчас только два: переводчик и аналитик.\n"
                "Остальное запускается отдельной командой и не требует выключения."
            ),
        )
        return

    if _looks_like_calendar_view_request(text):
        await _answer_long(
            message,
            _calendar_view_text(chat_id, user_id, text),
        )
        return

    if await handle_calendar_message(message, CALENDAR_STORAGE):
        return

    if re.match(r"^напомни\b", addressed_body, flags=re.I):
        await message.answer(
            "Не поняла время напоминания. Формат: "
            "«напомни сегодня в 13:00 ...», "
            "«напомни завтра в 8 утра ...», "
            "«напомни через 5 минут ...»."
        )
        return

    yt_query = _extract_manual_youtube_query(text)
    if yt_query:
        try:
            found = await _youtube_manual_search_for_message(message, yt_query)

            if not found:
                await message.answer("Не нашла.")
                return

            title = (found.get("title") or "").strip()
            url = (found.get("url") or "").strip()

            await _answer_long(message, f"{title}\n{url}".strip())
            return

        except Exception as e:
            print(f"[yt_manual] error: {type(e).__name__}: {e}", flush=True)
            await message.answer("Ошибка поиска.")
            return

    translator_on = _parse_translator_on_command(text)
    if translator_on:
        lang_a, lang_b = translator_on
        _set_translator_mode(chat_id, user_id, lang_a, lang_b)
        await message.answer(f"Режим переводчика включен: {lang_a} ⇄ {lang_b}.")
        return

    if _is_translator_off_command(text):
        _clear_translator_mode(chat_id, user_id)
        await message.answer("Режим переводчика отключен.")
        return

    clean_service_text = _strip_vesya_prefix(text).strip().lower()
    clean_service_text = re.sub(r"\s+", " ", clean_service_text).strip(" ?!.:;")

    if re.search(
        r"^(?:покажи\s+)?(?:свои\s+)?функции$|"
        r"^функции$|"
        r"^(?:покажи\s+)?что\s+ты\s+умеешь$|"
        r"^что\s+умеешь$|"
        r"^меню$|^help$|^помощь$",
        clean_service_text,
        flags=re.I,
    ):
        await _answer_long(
            message,
            (
                "Что умею:\n\n"

                "1. Переводчик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи переводчика с русского на английский\n"
                "   Веся включи переводчика с английского на русский\n"
                "   Выключить:\n"
                "   Веся выключи переводчика\n\n"

                "2. Аналитик — публичный режим\n"
                "   Включить:\n"
                "   Веся включи аналитика\n"
                "   Веся включи режим аналитика\n"
                "   Выключить:\n"
                "   Веся выключи аналитика\n"
                "   Веся отключи аналитика\n\n"

                "3. Напоминания\n"
                "   Отдельный режим включать не надо.\n"
                "   Просто напиши:\n"
                "   Веся напомни завтра в 9 утра позвонить Иванову\n"
                "   Веся напомни через 30 минут проверить духовку\n\n"

                "4. Новости\n"
                "   Отдельный режим включать и выключать не надо.\n"
                "   Просто напиши:\n"
                "   Веся новости\n\n"

                "5. YouTube / музыка / клипы\n"
                "   Просто попроси найти:\n"
                "   Веся дай что-нибудь из Offspring\n"
                "   Веся найди клип Metallica\n\n"

                "6. Фото и информация\n"
                "   Просто попроси:\n"
                "   Веся дай фото Козельска\n"
                "   Веся найди информацию по объекту\n\n"

                "7. Документы, фото и скриншоты\n"
                "   Просто пришли PDF, Word, Excel, фото или скриншот и задай вопрос.\n"
                "   Например:\n"
                "   Веся, что в этом документе главное?\n"
                "   Веся, проверь этот скриншот\n\n"

                "8. Обычный диалог\n"
                "   Просто пиши обычным языком.\n\n"

                "Важно:\n"
                "Постоянно включаемые публичные режимы сейчас только два: переводчик и аналитик.\n"
                "Остальное запускается отдельной командой и не требует выключения."
            ),
        )
        return

    mode = _get_translator_mode(chat_id, user_id)
    if mode:
        translated = await asyncio.to_thread(
            _translate_bidirectional,
            text,
            str(mode.get("lang_a") or ""),
            str(mode.get("lang_b") or ""),
        )

        if translated:
            await _answer_long(message, translated)
        else:
            await message.answer("Перевод не вышел.")
        return
        

    clean_action = _strip_vesya_prefix(text).strip().lower().strip(" ?!.,:;")

    beauty_text = _strip_vesya_prefix(text).strip()
    explicit_non_beauty = _is_explicit_non_beauty_request(beauty_text)

    direct_beauty_request = (
        not explicit_non_beauty
        and (
            _looks_like_direct_beauty_request(beauty_text)
            or chatgpt_dialog.detect_beauty_intent(beauty_text)
        )
    )

    beauty_followup_request = (
        _beauty_is_active(chat_id, user_id)
        and _beauty_followup(beauty_text)
    )

    if _beauty_is_active(chat_id, user_id) and not beauty_followup_request and not direct_beauty_request:
        BEAUTY_DIALOG_STATE.pop(_beauty_key(chat_id, user_id), None)

    if direct_beauty_request or beauty_followup_request:
        try:
            from beauty_pool import pick_unseen_beauty_clip, mark_beauty_clip_sent

            item = pick_unseen_beauty_clip(user_id)

            if not item:
                await message.answer("Пула красоты пока нет или всё уже показывала.")
                return

            clip_id = str(item.get("id") or "").strip()
            video_path = Path(str(item.get("path") or item.get("abs_path") or ""))

            if not clip_id or not video_path.exists():
                await message.answer("В пуле есть запись, но сам ролик не найден.")
                return

            caption = _make_beauty_caption(beauty_text, item, chat_id, user_id)

            _beauty_activate(chat_id, user_id)

            tmp_path = f"/tmp/vesya_beauty_{uuid.uuid4().hex}.mp4"
            shutil.copyfile(str(video_path), tmp_path)

            sent_ok = False
            last_send_error = None

            try:
                for attempt in range(3):
                    try:
                        await message.answer_video(
                            FSInputFile(tmp_path),
                            caption=caption,
                            request_timeout=int(os.getenv("V_VIDEO_SEND_TIMEOUT", "45")),
                        )
                        sent_ok = True
                        break
                    except Exception as e:
                        last_send_error = e
                        print(
                            f"[beauty] send attempt {attempt + 1}/3 failed: "
                            f"{type(e).__name__}: {e}",
                            flush=True,
                        )
                        await asyncio.sleep(2 + attempt * 2)

                if not sent_ok:
                    raise last_send_error or RuntimeError("beauty video send failed")

                mark_beauty_clip_sent(user_id, clip_id)
                return

            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        except Exception as e:
            print(f"[beauty] send failed: {type(e).__name__}: {e}", flush=True)
            await message.answer("Красиво не вышло. Что характерно.")
            return

    pending_action_words = {
        "ответь",
        "ответь на вопрос",
        "ответь на вопрос человеку",
        "ответь человеку",
        "ответь по сути",
        "ответь нормально",
        "ответь уже",
        "прокомментируй",
        "разбери",
        "проверь",
        "что думаешь",
        "что скажешь",
        "как тебе",
        "что это",
        "что это такое",
        "о чем это",
        "о чём это",
        "о чем это сообщение",
        "о чём это сообщение",
        "о чем сообщение",
        "о чём сообщение",
        "объясни это",
        "объясни что это",
        "опиши это",
        "опиши сообщение",
        "перескажи",
        "перескажи это",
        "переведи",
        "переведи это",
        "переведи на русский",
        "переведи это на русский",
        "перевод",
        "что значит",
        "что означает",
    }

    if (
        not _is_forwarded_message(message)
        and not message.reply_to_message
        and message.chat.type in ("private", "group", "supergroup")
    ):
        chat_id = int(message.chat.id)
        user_id = int(message.from_user.id) if message.from_user else 0

        if message.chat.type == "private" or chatgpt_dialog.persona.is_addressed(text):

            try:
                if is_analytics_active(chat_id, user_id):
                    pending_probe = {"wait_for_object": False}
                else:
                    direct_info_question = bool(re.search(
                        r"\b(кто\s+(?:такой|такая|такие)|что\s+такое|кто\s+это|что\s+это)\b",
                        _strip_vesya_prefix(text).strip().lower(),
                        flags=re.I,
                    ))

                    current_without_name = _strip_vesya_prefix(text).strip()

                    has_inline_object = bool(re.search(
                        r"[:：]\s*\S.{20,}|"
                        r"\n\s*\S.{20,}",
                        current_without_name,
                        flags=re.S,
                    ))

                    if direct_info_question or has_inline_object:
                        pending_probe = {"wait_for_object": False}
                    else:
                        pending_probe = chatgpt_dialog.semantic_needs_next_object(
                            text,
                            topic=_get_topic(chat_id, user_id),
                        )
                    
            except Exception:
                pending_probe = {"wait_for_object": False}

            if pending_probe.get("wait_for_object"):
                instruction = (
                    str(pending_probe.get("instruction") or "").strip()
                    or text
                )
                _set_pending_message_object_request(chat_id, user_id, instruction)
                print(
                    "[pending_message_object]",
                    {"instruction": instruction[:300]},
                    flush=True,
                )
                return

    if _is_forwarded_message(message):
        if await _try_universal_message_layer(message, text, event_type="forwarded"):
            return

        pending_key = (int(message.chat.id), int(message.from_user.id))
        pending = PENDING_FORWARD_ACTION.get(pending_key)

        pending_action = ""
        if pending:
            pending_ts = float(pending.get("ts") or 0)
            if (time.time() - pending_ts) <= PENDING_FORWARD_ACTION_TTL_SEC:
                pending_action = str(pending.get("action") or "").strip()
            PENDING_FORWARD_ACTION.pop(pending_key, None)

        inline_action = (
            bool(re.search(
r"\b(ответь|ответь\s+на\s+вопрос|ответь\s+по\s+сути|ответь\s+нормально|ответь\s+уже|прокомментируй|прокоммент|как\s+тебе|что\s+думаешь|что\s+скажешь|что\s+это|что\s+это\s+такое|о\s+ч[её]м\s+это|о\s+ч[её]м\s+сообщение|объясни|опиши|перескажи|разбери|проверь|переведи|перевод|что\s+значит|что\s+означает)\b",
                text,
                flags=re.I,
            ))
            or (
                chatgpt_dialog.persona.is_addressed(text)
                and "?" in text
            )
        )

        if pending_action or inline_action:
            obj_text = (message.text or message.caption or "").strip()

            if obj_text:
                enriched_text = obj_text

                try:
                    url = _extract_first_url(obj_text)

                    if url:
                        page_text = await asyncio.to_thread(_fetch_url_text, url)

                        if page_text:
                            enriched_text = (
                                f"{obj_text}\n\n"
                                f"Содержимое страницы по ссылке:\n"
                                f"{page_text[:8000]}"
                            )
                        else:
                            enriched_text = (
                                f"{obj_text}\n\n"
                                f"Ссылка найдена, но страницу прочитать не удалось: {url}"
                            )

                except Exception as e:
                    print(f"[forward_url] failed: {type(e).__name__}: {e}", flush=True)

                user_instruction = pending_action or text or "Опиши суть пересланного сообщения с учётом ссылки."

                dd = chatgpt_dialog.comment_text_object(user_instruction, enriched_text)

                if dd and (dd.reply or "").strip():
                    await _answer_long(message, dd.reply)
                    return

            await _handle_text_core(message, text, event_type=incoming_event_type)
            return

        return

    # =========================
    # ONE-SHOT RELAY MODE
    # =========================
    if _relay_is_armed(message):
        _relay_disarm(message)

        if not _is_admin_user(message):
            return

        ok = await _copy_to_main_group(message)
        if ok:
            await message.answer("кинула.")
        else:
            await message.answer("не вышло.")
        return
    # photo is handled below via reply-photo or LAST_USER_IMAGE_ID

    if message.from_user and _is_blocked_user(int(message.from_user.id)):
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    if is_analytics_active(chat_id, user_id):

        # analytics session must suppress
        # all pending object analyzers
        try:
            PENDING_MESSAGE_OBJECT_REQUEST.pop(
                (chat_id, user_id),
                None,
            )
        except Exception:
            pass

        try:
            PENDING_FORWARD_ACTION.pop(
                (chat_id, user_id),
                None,
            )
        except Exception:
            pass

        if await handle_analytics_message(message, text, _answer_long):
            return


    # В группе сюда доходят только разрешённые сообщения:
    # /команда, прямое обращение или reply на Весю.
    if message.chat.type in ("group", "supergroup"):
        is_cmd = text.lower().startswith("/")
        is_name = _is_direct_group_address(text)

        if is_name and not is_cmd:
            text = re.sub(
                r"^\s*(веся|веська|веслава|vesya|сергеевна)\s*[,.:;!\-]?\s+",
                "",
                text,
                flags=re.I,
            ).strip()
            if not text:
                await message.answer("да?")
                return
    # =========================
    # RELAY CANCEL / ARM
    # =========================
    if ("отбой" in text.lower() or "отмена" in text.lower()):
        if not _is_admin_user(message):
            return

        if _relay_is_armed(message):
            _relay_disarm(message)
            await message.answer("ладно, не шлём.")
        else:
            await message.answer("и не собирались.")
        return
    
    if await handle_analytics_message(message, text, _answer_long):
        return
    
    if await _try_admin_dialog_command(message, text):
        return

    if _relay_command_text(text):
        if not _is_admin_user(message):
            await message.answer("не тебе.")
            return

        _relay_arm(message)
        await message.answer("кидай следующим сообщением.")
        return

    if _wants_context_comment(text):
        handled = await _try_reply_context_comment(message, text)
        if handled:
            return

        _set_pending_message_object_request(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            text,
        )

        await message.answer("Кинь, посмотрю.")
        return

    dialog_text = text

    dialog_text = text

    # === REPLY CONTEXT FOR LLM ===
    if message.reply_to_message:
        r = message.reply_to_message

        # Если пользователь отвечает самой Весе — это продолжение конкретной reply-ветки.
        # Не превращаем в third-person meta-analysis, но обязательно даём LLM текст
        # сообщения Веси, на которое пришёл reply. Иначе короткие фразы типа
        # "это помогает?" цепляются к старой глобальной теме.
        if r.from_user and r.from_user.is_bot:
            replied_text = (
                r.text
                or r.caption
                or ""
            ).strip()

            if replied_text:
                thread_context = ""
                try:
                    if hasattr(vesya_memory, "render_reply_thread_context"):
                        thread_context = vesya_memory.render_reply_thread_context(
                            chat_id=int(message.chat.id),
                            reply_to_message_id=int(r.message_id),
                            limit=6,
                        )
                except Exception:
                    thread_context = ""

                dialog_text = (
                    (thread_context + "\n\n" if thread_context else "")
                    + "Пользователь отвечает реплаем на твою предыдущую реплику:\n"
                    f"«{replied_text}»\n\n"
                    "Текущая реплика пользователя:\n"
                    f"«{text}»\n\n"
                    "Отвечай именно в контексте этой reply-ветки. "
                    "Не переключайся на старые темы из общей истории, если текущая реплика понятна через replied-message. "
                    "Не пересказывай reply-chain и не говори о пользователе в третьем лице."
                )
            else:
                dialog_text = text
        else:
            current_author = (
                message.from_user.full_name
                if message.from_user
                else "пользователь"
            )

            replied_author = (
                r.from_user.full_name
                if r.from_user
                else "пользователь"
            )

            replied_text = (
                r.text
                or r.caption
                or ""
            ).strip()

            if replied_text:
                dialog_text = (
                    (
                        (
                            vesya_memory.render_reply_thread_context(
                                chat_id=int(message.chat.id),
                                reply_to_message_id=int(r.message_id),
                                limit=6,
                            )
                            + "\n\n"
                        )
                        if hasattr(vesya_memory, "render_reply_thread_context")
                        else ""
                    )
                    + f"Контекст: {current_author} отвечает на сообщение от {replied_author}:\n"
                    f"«{replied_text}»\n\n"
                    f"Текущая реплика {current_author}, на которую нужно ответить напрямую:\n"
                    f"«{text}»\n\n"
                    "Ответь напрямую текущему пользователю от лица Веси. "
                    "Не пересказывай его реплику. "
                    "Не называй его 'собеседник', 'автор' или 'он'. "
                    "Не анализируй интонацию и намерения, если пользователь прямо этого не просит."
                )
            else:
                dialog_text = (
                    f"Контекст: {current_author} отвечает на сообщение от {replied_author}.\n\n"
                    f"Текущая реплика {current_author}, на которую нужно ответить напрямую:\n"
                    f"«{text}»\n\n"
                    "Ответь напрямую текущему пользователю от лица Веси. "
                    "Не называй его 'собеседник', 'автор' или 'он'."
                )

    elif message.chat.type in ("group", "supergroup") and "is_reply_to_bot" in locals() and is_reply_to_bot:
        if not chatgpt_dialog.persona.is_addressed(dialog_text):
            dialog_text = f"Веся, {dialog_text}"

    print(f"[route] text={text!r}", flush=True)

    # === GMAIL CONNECT COMMAND ===
    if "подключи почту" in text.lower():
        user_id = message.from_user.id
        url = f"https://vesya-auth.onrender.com/auth/google/start?user_id={user_id}"
        await message.answer(f"Подключи почту:\n{url}")
        return
        
    # === GMAIL CHECK COMMAND ===
    if "проверь почту" in text.lower():
        await message.answer("смотрю почту...")

        try:
            import requests
            from openai import OpenAI

            def _ru_summary(s: str) -> str:
                key = (os.getenv("OPENAI_API_KEY") or "").strip()
                if not key:
                    return s[:260]
                try:
                    client = OpenAI(api_key=key)
                    r = client.responses.create(
                        model=os.getenv("V_DIALOG_MODEL", "gpt-4o-mini"),
                        input=[
                            {"role": "system", "content": "Кратко переведи и перескажи письмо по-русски в 1-2 строки. Без воды."},
                            {"role": "user", "content": s[:2500]},
                        ],
                    )
                    return (getattr(r, "output_text", "") or s[:260]).strip()
                except Exception:
                    return s[:260]

            supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or ""
            user_id = int(message.from_user.id)

            r = requests.get(
                f"{supabase_url}/rest/v1/gmail_accounts",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
                params={"select": "creds_json", "user_id": f"eq.{user_id}", "order": "id.desc", "limit": "1"},
                timeout=20,
            )

            rows = r.json()
            if not rows:
                await message.answer("почта не подключена")
                return

            creds = rows[0].get("creds_json") or {}
            refresh_token = creds.get("refresh_token")
            if not refresh_token:
                await message.answer("refresh_token не найден")
                return

            token_res = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=20,
            ).json()

            access_token = token_res.get("access_token")
            if not access_token:
                await message.answer(f"не смогла обновить токен: {token_res}")
                return

            gmail_res = requests.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"maxResults": "5", "q": "in:inbox"},
                timeout=20,
            ).json()

            messages = gmail_res.get("messages") or []
            if not messages:
                await message.answer("в inbox писем не вижу")
                return

            saved = []
            for idx, m in enumerate(messages[:5], start=1):
                msg_id = m.get("id")
                if not msg_id:
                    continue

                detail = requests.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    timeout=20,
                ).json()

                headers = {h.get("name"): h.get("value") for h in detail.get("payload", {}).get("headers", [])}
                subj = headers.get("Subject", "без темы")
                frm = headers.get("From", "")
                date = headers.get("Date", "")
                snippet = detail.get("snippet", "")

                saved.append({
                    "id": msg_id,
                    "from": frm,
                    "subject": subj,
                    "date": date,
                    "snippet": snippet,
                    "access_token": access_token,
                })

                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Открыть", callback_data=f"gmail_open_id:{msg_id}"),
                    InlineKeyboardButton(text="Удалить", callback_data=f"gmail_del_id:{msg_id}"),
                ]])

                await message.answer(
                    f"{idx}. 📩 {subj}\n"
                    f"От: {frm}\n"
                    f"Кратко: {_ru_summary(snippet)}",
                    reply_markup=kb,
                )

            GMAIL_LAST_MESSAGES[user_id] = saved

        except Exception as e:
            print(f"[gmail_check] failed: {type(e).__name__}: {e}", flush=True)
            await message.answer(f"почту не прочитала: {type(e).__name__}: {e}")

        return
    
    # === GMAIL OPEN MESSAGE COMMAND ===
    if "письмо " in text.lower():
        try:
            import base64
            import requests

            user_id = int(message.from_user.id)
            mm = re.search(r"письмо\s+(\d+)", text.lower())
            if not mm:
                return

            n = int(mm.group(1))
            cached = GMAIL_LAST_MESSAGES.get(user_id) or []

            if n < 1 or n > len(cached):
                await message.answer("такого номера письма нет. сначала: Веся проверь почту")
                return

            item = cached[n - 1]
            access_token = item["access_token"]
            msg_id = item["id"]

            detail = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "full"},
                timeout=20,
            ).json()

            def _walk_parts(payload):
                if not payload:
                    return ""
                body = payload.get("body", {}) or {}
                data = body.get("data")
                mime = payload.get("mimeType", "")
                if data and ("text/plain" in mime or mime == ""):
                    try:
                        raw = base64.urlsafe_b64decode(data + "===")
                        return raw.decode("utf-8", errors="replace")
                    except Exception:
                        return ""
                for p in payload.get("parts", []) or []:
                    got = _walk_parts(p)
                    if got:
                        return got
                return ""

            full_text = _walk_parts(detail.get("payload") or {})
            if not full_text:
                full_text = detail.get("snippet", "")

            def _translate_full_email_to_ru(s: str) -> str:
                src = (s or "").strip()
                if not src:
                    return ""

                key = (os.getenv("OPENAI_API_KEY") or "").strip()
                if not key:
                    return src

                try:
                    from openai import OpenAI

                    client = OpenAI(api_key=key)
                    r = client.responses.create(
                        model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
                        input=[
                            {
                                "role": "system",
                                "content": (
                                    "Переведи письмо на русский полностью и аккуратно.\n"
                                    "Не пересказывай кратко.\n"
                                    "Не сокращай.\n"
                                    "Сохрани смысл всех абзацев, чисел, дат, ссылок и условий.\n"
                                    "Рекламные подписи тоже переведи, если они есть в тексте.\n"
                                    "Верни только перевод."
                                ),
                            },
                            {
                                "role": "user",
                                "content": src[:20000],
                            },
                        ],
                    )

                    return (getattr(r, "output_text", "") or "").strip() or src

                except Exception as e:
                    print(f"[gmail_translate] failed: {type(e).__name__}: {e}", flush=True)
                    return src

            translated_text = _translate_full_email_to_ru(full_text)

            out = (
                f"📩 {item.get('subject')}\n"
                f"От: {item.get('from')}\n"
                f"Дата: {item.get('date')}\n\n"
                f"{translated_text}"
            )

            await _answer_long(message, out)

        except Exception as e:
            print(f"[gmail_open] failed: {type(e).__name__}: {e}", flush=True)
            await message.answer(f"письмо не открыла: {type(e).__name__}: {e}")

        return
    
    # === GMAIL DISCONNECT COMMAND ===
    if "отключи почту" in text.lower():
        if message.chat.type != "private":
            await message.answer("почту отключаем только в личке.")
            return

        try:
            import requests

            supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or ""
            user_id = int(message.from_user.id)

            r = requests.delete(
                f"{supabase_url}/rest/v1/gmail_accounts",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Prefer": "return=minimal",
                },
                params={"user_id": f"eq.{user_id}"},
                timeout=20,
            )

            if r.status_code >= 300:
                await message.answer(f"не отключила: {r.status_code} {r.text[:300]}")
                return

            GMAIL_LAST_MESSAGES.pop(user_id, None)
            await message.answer("почту отключила.")

        except Exception as e:
            print(f"[gmail_disconnect] failed: {type(e).__name__}: {e}", flush=True)
            await message.answer(f"не отключила: {type(e).__name__}: {e}")

        return


    # =========================
    # PHOTO CONTEXT: describe only when user asked
    # =========================
    try:
        img_bytes = None

        # detect "user asked to describe photo"
        t = text.lower()
        wants_photo = (
            "что на фото" in t
            or "что ты видишь" in t
            or "что это" in t
            or "что за " in t
            or "где это" in t
            or "что здесь" in t
            or "что изображено" in t
            or "где хранится" in t
            or "кто автор" in t
            or "какой стиль" in t
            or "какая эпоха" in t
            or "чья это работа" in t
            or t.startswith("опиши фото")
            or t.startswith("опиши изображ")
            or "опиши фото" in t
        )

        if wants_photo:
            # 1) If user replied to a photo/document → use that
            r = message.reply_to_message
            if r:
                img_id = None
                if getattr(r, "photo", None):
                    img_id = r.photo[-1].file_id
                elif getattr(r, "document", None) and (getattr(r.document, "mime_type", "") or "").startswith("image/"):
                    img_id = r.document.file_id

                if img_id:
                    raw = await _download_tg_file_bytes(message.bot, img_id)
                    img_bytes = _shrink_jpeg_bytes(raw)

            # 2) If still none → use last saved photo for this user
            if img_bytes is None:
                uid = int(message.from_user.id) if message.from_user else 0
                fid = LAST_USER_IMAGE_ID.get((int(message.chat.id), uid))
                if fid:
                    raw = await _download_tg_file_bytes(message.bot, fid)
                    img_bytes = _shrink_jpeg_bytes(raw)

        plain_dialog_followup_early = _looks_like_plain_dialog_followup(text)

        # If we have image bytes → call vision describe,
        # but never hijack plain dialog follow-ups like "не понял объясни подробнее".
        if img_bytes is not None and not plain_dialog_followup_early:
            dd = chatgpt_dialog.describe_or_compare_photo(text, img_bytes)
            if dd and (dd.reply or "").strip():
                await _answer_long(message, dd.reply)
                return

    except Exception as e:
        print(f"[IMG] photo describe routing error: {type(e).__name__}: {e}", flush=True)    
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    # reply to bot-sent content should stay LLM-chat,
    # not trigger fresh content broadcast
    r = message.reply_to_message

    def _has_fb_buttons(msg) -> bool:
        try:
            rm = getattr(msg, "reply_markup", None)
            kb = getattr(rm, "inline_keyboard", None) or []
            for row in kb:
                for btn in row:
                    data = (getattr(btn, "callback_data", "") or "")
                    if data.startswith("fb:"):
                        return True
        except Exception:
            pass
        return False

    is_reply_to_bot_content = bool(
        r
        and (
            _has_fb_buttons(r)
            or (
                r.from_user
                and r.from_user.is_bot
                and (
                    getattr(r, "video", None)
                    or getattr(r, "video_note", None)
                    or getattr(r, "photo", None)
                    or (
                        getattr(r, "document", None)
                        and (
                            (getattr(r.document, "mime_type", "") or "").startswith("video/")
                            or (getattr(r.document, "mime_type", "") or "").startswith("image/")
                        )
                    )
                )
            )
        )
    )

    plain_dialog_followup = _looks_like_plain_dialog_followup(text)

    if is_reply_to_bot_content:
        await _handle_text_core(message, f"Веся, {text}", event_type=incoming_event_type)
        return

    if plain_dialog_followup:
        dialog_text = text

    await _handle_text_core(message, dialog_text, event_type=incoming_event_type)
    return

# =====================
# INGEST24 LOOP (06:00 MSK)
# =====================

from datetime import datetime, timedelta, timezone

# =========================
# POOLS (24h): memes + videos
# =========================
POOL_TTL_SEC = int(os.getenv("V_POOL_TTL_SEC", str(24 * 3600)))

def _pool_path(kind: str, user_id: int) -> Path:
    return DATA_DIR / f"{kind}_pool_{int(user_id)}.json"

def _pool_raw_path(kind: str) -> Path:
    d = datetime.now(timezone.utc).strftime("%Y%m%d")
    return DATA_DIR / f"{kind}_pool_raw_{d}.json"

def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass

def _pool_is_fresh(pool: dict) -> bool:
    try:
        ts = float(pool.get("ts") or 0)
        return (time.time() - ts) < POOL_TTL_SEC
    except Exception:
        return False
def _refresh_video_pool(user_id: int) -> dict:
    COLLECT_N = int(os.getenv("V_VIDEO_COLLECT_N", "80"))

    # scan raw files directly (rank_top_n currently returns 0)
    # build item_id as: {src}/{msg_id}.mp4  (same as old sent_video ids)
    raw_dir = DATA_DIR / "raw"
    exts = {".mp4", ".webm", ".mov", ".mkv"}

    files = []
    try:
        for p in raw_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in exts:
                files.append(p)
    except Exception:
        files = []

    # newest first
    try:
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        pass

    out = []
    seen = set()
    seen_hashes = set()

    def _file_sha1(path: Path) -> str:
        import hashlib
        h = hashlib.sha1()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    for p in files:
        if len(out) >= COLLECT_N:
            break

        meta_path = Path(str(p) + ".meta.json")
        src = ""
        msg_id = None
        views = 0
        forwards = 0
        replies = 0
        reactions_total = 0
        score = 0.0

        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                src = (meta.get("src") or "").strip()
                msg_id = meta.get("msg_id")

                views = int(meta.get("views", 0) or 0)
                forwards = int(meta.get("forwards", 0) or 0)
                replies = int(meta.get("replies", 0) or 0)
                reactions_total = int(meta.get("reactions_total", 0) or 0)

                # simple quality score
                score = (views * 0.15) + (forwards * 4.0) + (replies * 3.0) + (reactions_total * 5.0)
            except Exception:
                src = ""
                msg_id = None
                views = forwards = replies = reactions_total = 0
                score = 0.0

        # fallback: if meta missing, skip (better than wrong ids)
        if not src or not msg_id:
            continue

        item_id = f"{src}/{msg_id}{p.suffix.lower()}"

        if item_id in seen:
            continue

        if not p.exists():
            continue

        try:
            file_sha1 = _file_sha1(p)
        except Exception:
            file_sha1 = ""

        if file_sha1 and file_sha1 in seen_hashes:
            continue

        seen.add(item_id)
        if file_sha1:
            seen_hashes.add(file_sha1)

        out.append({
            "item_id": item_id,
            "abs_path": str(p),
            "ts": int(time.time()),
            "src": src,
            "msg_id": msg_id,
            "views": views,
            "forwards": forwards,
            "replies": replies,
            "reactions_total": reactions_total,
            "score": score,
        })
    # best first: by engagement score, tie-breaker by recency already in file order
    try:
        out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    except Exception:
        pass

    pool = {"ts": int(time.time()), "items": out}
    _save_json(_pool_raw_path("video"), pool)           # what was found today (debug)
    _save_json(_pool_path("video", user_id), pool)      # work pool
    print(f"[pool] video refreshed items={len(out)}", flush=True)
    return pool

def _refresh_meme_pool(user_id: int) -> dict:
    COLLECT_N = int(os.getenv("V_MEME_COLLECT_N", "120"))
    items = rank_memes(user_id=user_id, n=COLLECT_N)

    out = []
    seen = set()
    for it in items:
        item_id = (getattr(it, "item_id", "") or "").strip()
        abs_path = (getattr(it, "abs_path", "") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)

        p = Path(abs_path) if abs_path else None
        if not p or (not p.exists()):
            continue

        cap = ""
        src = (getattr(it, "src", "") or "").strip()

        mp = Path(str(p) + ".meta.json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                cap = (meta.get("caption") or "").strip()
                if not src:
                    src = (meta.get("src") or "").strip()
            except Exception:
                pass

        out.append({
            "item_id": item_id,
            "abs_path": str(p),
            "caption": cap,
            "src": src,
            "ts": int(time.time()),
        })

    pool = {"ts": int(time.time()), "items": out}
    _save_json(_pool_raw_path("meme"), pool)            # what was found today (debug)
    _save_json(_pool_path("meme", user_id), pool)       # work pool
    print(f"[pool] meme refreshed items={len(out)}", flush=True)
    return pool

MSK = timezone(timedelta(hours=3))

async def ingest24_loop(bot: Bot) -> None:
    await asyncio.sleep(10)

    while True:
        now = datetime.now(MSK)
        next_run = now.replace(hour=5, minute=0, second=0, microsecond=0)

        if now >= next_run:
            next_run += timedelta(days=1)

        wait_sec = (next_run - now).total_seconds()
        print(f"[ingest24] next run in {int(wait_sec)} sec", flush=True)

        await asyncio.sleep(wait_sec)

        print("[ingest24] starting ingest_hours(24)", flush=True)

        try:
            async with TG_LOCK:
                await ingest_hours(24)

            print("[ingest24] auto-send disabled; ingest only", flush=True)

            try:
                from beauty_collector import collect_beauty_hours

                beauty_hours = int(os.getenv("BEAUTY_COLLECT_HOURS", "24"))

                async with TG_LOCK:
                    await collect_beauty_hours(beauty_hours)

                print(f"[beauty24] collect done hours={beauty_hours}", flush=True)

            except Exception as e:
                print(f"[beauty24] collect error: {type(e).__name__}: {e}", flush=True)

            # при необходимости можно оставить обновление пулов
            try:
                _refresh_video_pool(MAIN_GROUP_ID)
            except Exception as e:
                print(f"[pool] video refresh error: {e}", flush=True)

            try:
                _refresh_meme_pool(MAIN_GROUP_ID)
            except Exception as e:
                print(f"[pool] meme refresh error: {e}", flush=True)

        except Exception as e:
            print(f"[ingest24] error: {e}", flush=True)

async def heartbeat_loop() -> None:
    while True:
        _log("heartbeat")
        await asyncio.sleep(300)

async def gmail_poll_loop() -> None:
    await asyncio.sleep(30)

    while True:
        try:
            await _gmail_poll_once()
        except Exception as e:
            print(f"[gmail_poll] failed: {type(e).__name__}: {e}", flush=True)

        await asyncio.sleep(GMAIL_POLL_INTERVAL_SEC)


async def _gmail_poll_once() -> None:
    import requests
    from datetime import datetime, timezone
    from openai import OpenAI

    def _parse_iso_ms(value: str | None) -> int:
        if not value:
            return 0
        try:
            v = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    def _ru_summary(text: str) -> str:
        key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not key:
            return (text or "")[:260]

        try:
            client = OpenAI(api_key=key)
            r = client.responses.create(
                model=os.getenv("V_DIALOG_MODEL", "gpt-4o-mini"),
                input=[
                    {
                        "role": "system",
                        "content": "Кратко переведи и перескажи письмо по-русски в 1-2 строки. Без воды.",
                    },
                    {
                        "role": "user",
                        "content": (text or "")[:2500],
                    },
                ],
            )
            return (getattr(r, "output_text", "") or text[:260]).strip()
        except Exception:
            return (text or "")[:260]

    supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or ""

    if not supabase_url or not supabase_key:
        print("[gmail_poll] SUPABASE_URL/SUPABASE_KEY empty", flush=True)
        return

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }

    acc_res = requests.get(
        f"{supabase_url}/rest/v1/gmail_accounts",
        headers=headers,
        params={
            "select": "id,user_id,creds_json,last_checked,created_at,lang",
            "order": "id.desc",
            "limit": "100",
        },
        timeout=20,
    )

    if acc_res.status_code >= 300:
        print(f"[gmail_poll] supabase accounts failed: {acc_res.status_code} {acc_res.text}", flush=True)
        return

    accounts = acc_res.json() or []

    seen_users = set()

    for acc in accounts:
        uid_key = int(acc.get("user_id") or 0)
        if not uid_key:
            continue
        if uid_key in seen_users:
            continue
        seen_users.add(uid_key)
        acc_id = acc.get("id")
        user_id = int(acc.get("user_id") or 0)

        if not acc_id or not user_id:
            continue

        creds = acc.get("creds_json") or {}
        refresh_token = creds.get("refresh_token")

        if not refresh_token:
            continue

        last_ms = _parse_iso_ms(acc.get("last_checked") or acc.get("created_at"))

        token_res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        ).json()

        access_token = token_res.get("access_token")
        if not access_token:
            print(f"[gmail_poll] token refresh failed user_id={user_id}: {token_res}", flush=True)
            continue

        list_res = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"maxResults": "10", "q": "in:inbox"},
            timeout=20,
        ).json()

        messages = list_res.get("messages") or []
        new_items = []

        for m in messages:
            msg_id = m.get("id")
            if not msg_id:
                continue

            detail = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
                timeout=20,
            ).json()

            internal_ms = int(detail.get("internalDate") or 0)
            if internal_ms <= last_ms:
                continue

            msg_headers = {
                h.get("name"): h.get("value")
                for h in detail.get("payload", {}).get("headers", [])
            }

            new_items.append({
                "id": msg_id,
                "from": msg_headers.get("From", ""),
                "subject": msg_headers.get("Subject", "без темы"),
                "date": msg_headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
                "access_token": access_token,
            })

        if new_items:
            GMAIL_LAST_MESSAGES[user_id] = new_items[:10]

            for idx, item in enumerate(new_items[:5], start=1):
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Открыть", callback_data=f"gmail_open_id:{item.get('id')}"),
                    InlineKeyboardButton(text="Удалить", callback_data=f"gmail_del_id:{item.get('id')}"),
                ]])

                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"{idx}. 📩 {item.get('subject')}\n"
                        f"От: {item.get('from')}\n"
                        f"Кратко: {_ru_summary(item.get('snippet') or '')}"
                    ),
                    reply_markup=kb,
                )

        now_iso = datetime.now(timezone.utc).isoformat()

        requests.patch(
            f"{supabase_url}/rest/v1/gmail_accounts",
            headers={
                **headers,
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={"id": f"eq.{acc_id}"},
            json={"last_checked": now_iso},
            timeout=20,
        )
# =========================
# START
# =========================

async def main() -> None:
    _log("starting aiogram polling")
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(ingest24_loop(bot))
    asyncio.create_task(gmail_poll_loop())
    asyncio.create_task(calendar_loop(bot, CALENDAR_STORAGE))
    await dp.start_polling(bot)

@dp.callback_query(F.data.startswith("an:"))
async def on_analytics_callback(cb):
    if await handle_analytics_callback(cb, _answer_long):
        return

@dp.callback_query(F.data.startswith("fb:"))
async def on_feedback(cb):
    parts = cb.data.split(":")
    action = parts[1]
    item_id = ":".join(parts[2:])   # ← FIX

    print(f"[feedback] action={action} item_id={item_id} user={cb.from_user.id}", flush=True)

    import time
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\t{cb.from_user.id}\t{action}\t{item_id}\n")

    try:
        await cb.answer("принято 🔥")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("gmail_open_id:"))
@dp.callback_query(F.data.startswith("gmail_open:"))
async def on_gmail_open(cb):
    try:
        import base64
        import requests
        from openai import OpenAI

        msg_id = cb.data.split(":", 1)[1].strip()
        user_id = int(cb.from_user.id)
        cached = GMAIL_LAST_MESSAGES.get(user_id) or []

        item = next((x for x in cached if x.get("id") == msg_id), None)
        if not item:
            await cb.message.answer("кэш письма сдох. Проверь почту заново.")
            return

        access_token = item["access_token"]

        detail = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
            timeout=20,
        ).json()

        def _decode_gmail_body(data: str) -> str:
            if not data:
                return ""
            try:
                pad = "=" * (-len(data) % 4)
                return base64.urlsafe_b64decode((data + pad).encode("utf-8")).decode("utf-8", errors="replace")
            except Exception:
                return ""

        def _html_to_text(s: str) -> str:
            s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", s or "")
            s = re.sub(r"(?i)<br\s*/?>", "\n", s)
            s = re.sub(r"(?i)</p\s*>", "\n\n", s)
            s = re.sub(r"(?s)<[^>]+>", " ", s)
            s = html.unescape(s)
            s = re.sub(r"[ \t]+", " ", s)
            s = re.sub(r"\n\s+\n", "\n\n", s)
            return s.strip()

        def _walk(payload):
            plain_parts = []
            html_parts = []

            def rec(p):
                if not p:
                    return

                mime = (p.get("mimeType") or "").lower()
                body = p.get("body", {}) or {}
                data = body.get("data")

                if data:
                    txt = _decode_gmail_body(data)
                    if txt:
                        if mime == "text/plain":
                            plain_parts.append(txt)
                        elif mime == "text/html":
                            html_parts.append(_html_to_text(txt))

                for child in p.get("parts", []) or []:
                    rec(child)

            rec(payload)

            if plain_parts:
                return "\n\n".join(x.strip() for x in plain_parts if x.strip()).strip()

            if html_parts:
                return "\n\n".join(x.strip() for x in html_parts if x.strip()).strip()

            return ""

        full_text = _walk(detail.get("payload") or {}) or detail.get("snippet", "")

        translated = chatgpt_dialog.translate_to_ru(full_text)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Удалить", callback_data=f"gmail_del_id:{msg_id}"),
        ]])

        full_reply = (
            f"📩 {item.get('subject')}\n"
            f"От: {item.get('from')}\n"
            f"Дата: {item.get('date')}\n\n"
            f"{translated}"
        )

        await _answer_long(cb.message, full_reply)

        await cb.message.answer("Действие с письмом:", reply_markup=kb)
        await cb.answer()

    except Exception as e:
        print(f"[gmail_open] failed: {type(e).__name__}: {e}", flush=True)
        await cb.message.answer(f"письмо не открыла: {type(e).__name__}: {e}")

@dp.callback_query(F.data.startswith("gmail_del_id:"))
@dp.callback_query(F.data.startswith("gmail_del:"))
async def on_gmail_delete(cb):
    try:
        import requests

        msg_id = cb.data.split(":", 1)[1].strip()
        user_id = int(cb.from_user.id)
        cached = GMAIL_LAST_MESSAGES.get(user_id) or []

        item = next((x for x in cached if x.get("id") == msg_id), None)
        if not item:
            item = {"id": msg_id, "subject": "письмо"}

        supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or ""

        r = requests.get(
            f"{supabase_url}/rest/v1/gmail_accounts",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
            params={
                "select": "creds_json",
                "user_id": f"eq.{user_id}",
                "order": "id.desc",
                "limit": "1",
            },
            timeout=20,
        )

        rows = r.json()
        if not rows:
            await cb.message.answer("почта не подключена")
            return

        creds = rows[0].get("creds_json") or {}
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            await cb.message.answer("refresh_token не найден")
            return

        token_res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        ).json()

        access_token = token_res.get("access_token")
        if not access_token:
            await cb.message.answer(f"не смогла обновить токен: {token_res}")
            return

        rr = requests.post(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}/trash",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )

        if rr.status_code >= 300:
            body = rr.text[:500]
            if rr.status_code in (401, 403):
                await cb.message.answer(
                    "Не удалила. У почты нет права на удаление. "
                    "Нужно переподключить Gmail со scope gmail.modify."
                )
            else:
                await cb.message.answer(f"не удалила: {rr.status_code} {body}")
            return

        try:
            GMAIL_LAST_MESSAGES[user_id] = [x for x in cached if x.get("id") != msg_id]
        except Exception:
            pass

        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cb.message.answer(f"удалила: {item.get('subject')}")
        await cb.answer("удалено")

    except Exception as e:
        print(f"[gmail_del] failed: {type(e).__name__}: {e}", flush=True)
        await cb.message.answer(f"не удалила: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())

def get_recent_user_events(
    *,
    chat_id: int,
    user_id: int,
    minutes: int = 60 * 24 * 7,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    events = get_recent_events(minutes=minutes)
    out: List[Dict[str, Any]] = []

    for e in events:
        try:
            if int(e.get("chat_id")) != int(chat_id):
                continue
            if int(e.get("user_id")) != int(user_id):
                continue
            out.append(e)
        except Exception:
            continue

    return out[-max(1, int(limit)):]


def render_user_memory_context(
    *,
    chat_id: int,
    user_id: int,
    minutes: int = 60 * 24 * 7,
    limit: int = 8,
) -> str:
    events = get_recent_user_events(
        chat_id=chat_id,
        user_id=user_id,
        minutes=minutes,
        limit=limit,
    )

    if not events:
        return ""

    lines = []
    for e in events:
        intent = (e.get("intent") or "").strip()
        text = (e.get("text") or "").strip().replace("\n", " ")
        reply = (e.get("reply") or "").strip().replace("\n", " ")

        if not text:
            continue

        if len(text) > 220:
            text = text[:220].rstrip() + "…"
        if len(reply) > 180:
            reply = reply[:180].rstrip() + "…"

        if reply:
            lines.append(f"- intent={intent}; user: {text}; vesya: {reply}")
        else:
            lines.append(f"- intent={intent}; user: {text}")

    if not lines:
        return ""

    return (
        "Память последних взаимодействий с этим пользователем. "
        "Используй только как контекст, не пересказывай память напрямую:\n"
        + "\n".join(lines)
    )