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
GMAIL_LAST_MESSAGES = {}  # user_id -> list[dict]
GMAIL_POLL_INTERVAL_SEC = int(os.getenv("GMAIL_POLL_INTERVAL_SEC", "900"))
GMAIL_LAST_MESSAGES = {}  # user_id -> list[dict]# last image per (chat_id, user_id) to support "опиши фото" without reply
LAST_USER_IMAGE_ID = {}

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

TOPIC_TTL_SEC = int(os.getenv("V_TOPIC_TTL_SEC", str(7 * 24 * 3600)))
TOPIC_PATH = DATA_DIR / "vesya_topics.json"

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

        vesya_memory.append_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id,
            "chat_type": message.chat.type,
            "user_id": user_id,
            "message_id": int(message.message_id),
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

def _wants_context_comment(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False

    # Личный вопрос к Весе — отвечаем на вопрос, НЕ комментируем вложенный/пересланный объект.
    if re.search(
        r"\b(ты|тебе|тебя|твой|твоя|твои|хочешь|можешь|будешь|стала бы|согласна|нравится ли тебе)\b",
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
            dd = chatgpt_dialog.comment_text_object(user_text, obj_text)
            if dd and (dd.reply or "").strip():
                await _answer_long(message, dd.reply)
                return True

    except Exception as e:
        print(f"[context_comment] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"не смогла прокомментировать: {type(e).__name__}: {e}")
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
    t = (text or "").strip().lower()
    if not t:
        return False

    return any(x in t for x in (
        "а что за музыка",
        "что за музыка",
        "что за трек",
        "что за песня",
        "кто поет",
        "кто поёт",
        "а подробнее",
        "почему",
        "а почему",
        "что там",
        "что она",
        "что он",
        "что они",
        "это правда",
        "а это",
        "и что",
        "ну и",
        "в смысле",
        "объясни",
        "поясни",
        "разбери",
    ))

# =========================
# NEWS RUNNER (calls Telethon inside news_digest)
# =========================

def _strip_vesya_prefix(text: str) -> str:
    t = (text or "").strip()
    return re.sub(
        r"^\s*(веся|веська|веслава|vesya|сергеевна)\s*[,.:;!\-]?\s*",
        "",
        t,
        flags=re.I,
    ).strip()

async def _answer_long(message: Message, text: str, *, chunk_size: int = 3500) -> None:
    t = (text or "").strip()
    if not t:
        return

    while t:
        part = t[:chunk_size]
        cut = max(part.rfind("\n\n"), part.rfind("\n"), part.rfind(". "))
        if cut > 1200:
            part = part[:cut + 1]

        await message.answer(part.strip())
        t = t[len(part):].strip()

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
            language="ru",
        )

        return (getattr(tr, "text", "") or "").strip()

    except Exception as e:
        print(f"[voice] transcribe failed: {type(e).__name__}: {e}", flush=True)
        return ""
    
def _tts_to_ogg_bytes(text: str) -> bytes:
    t = (text or "").strip()
    if not t:
        return b""

    api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        return b""

    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=api_key)

        # voice reply должен быть коротким
        t = t[:900]

        audio = client.text_to_speech.convert(
            voice_id="EXAVITQu4vr4xnSDxMaL",
            output_format="opus_48000_128",
            text=t,
            model_id="eleven_multilingual_v2",
        )

        return b"".join(audio)

    except Exception as e:
        print(f"[voice] elevenlabs tts failed: {type(e).__name__}: {e}", flush=True)
        return b""
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

def _extract_web_search_query(text: str) -> str:
    q = _strip_vesya_prefix(text)
    q = re.sub(
        r"^\s*(найди|поищи|загугли|посмотри в интернете|поищи в интернете)\s+",
        "",
        q,
        flags=re.I,
    ).strip()
    return q


async def _run_web_search_for_message(message: Message, query: str) -> None:
    query = (query or "").strip()
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
                    "count": 5,
                    "search_lang": "ru",
                    "country": "RU",
                },
                timeout=20,
            )
            r.raise_for_status()
            return r.json()

        data = await asyncio.to_thread(_search)
        results = (data.get("web") or {}).get("results") or []

        if not results:
            await message.answer("Ничего внятного не нашла. Интернет тоже умеет молчать.")
            return

        compact = []
        for x in results[:5]:
            compact.append({
                "title": (x.get("title") or "").strip(),
                "url": (x.get("url") or "").strip(),
                "description": (x.get("description") or "").strip(),
            })

        client = OpenAI()
        resp = client.responses.create(
            model=os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini"),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты — Веся. Пользователь попросил найти информацию в интернете. "
                        "На основе результатов поиска дай короткий ответ по-русски. "
                        "Сначала факты, потом короткая холодная интонация. "
                        "Не хамить пользователю. Не уходить в команды. "
                        "Формат: 2–5 коротких строк. Если источники слабые — прямо скажи."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Запрос: {query}\n\n"
                        f"Результаты поиска JSON:\n{json.dumps(compact, ensure_ascii=False)}"
                    ),
                },
            ],
        )

        summary = (getattr(resp, "output_text", "") or "").strip()
        if not summary:
            summary = "Нашла, но пересказать красиво не вышло. Очень по-человечески."

        links = []
        for i, x in enumerate(compact[:3], start=1):
            title = x.get("title") or "источник"
            url = x.get("url") or ""
            if url:
                links.append(f"{i}. {title}\n{url}")

        await _answer_long(message, summary + "\n\n" + "\n".join(links))

    except Exception as e:
        print(f"[web_search] failed: {type(e).__name__}: {e}", flush=True)
        await message.answer(f"поиск сломался: {type(e).__name__}: {e}")

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

        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )
        caption = (message.caption or "").strip()
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
            if not _img_should_react(int(message.chat.id)):
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

            if message.chat.type in ("group", "supergroup"):
                if not _img_should_react(int(message.chat.id)):
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

async def _handle_text_core(message: Message, text: str, *, event_type: str = "text") -> None:
    """
    Shared semantic core for normal text and transcribed voice.
    Voice must not have its own intelligence: it becomes text and uses the same router/executors.
    """
    text = (text or "").strip()
    if not text:
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    dialog_text = text

    if message.reply_to_message:
        r = message.reply_to_message

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
                f"Веся, сообщение от {current_author}. "
                f"Он отвечает на сообщение пользователя {replied_author}: "
                f"«{replied_text}». "
                f"В его текущем сообщении местоимения вроде 'он', 'его', 'ему' относятся к {replied_author}. "
                f"Текущий текст: «{text}»."
            )

    decision = chatgpt_dialog.decide(chat_id, user_id, dialog_text)

    print(f"[route][{event_type}] intent={decision.intent} reply={decision.reply!r}", flush=True)

    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()

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
            await _send_reply_for_event(message, reply, event_type=event_type)
        return

    if intent == "news":
        if reply:
            await _send_reply_for_event(message, reply, event_type=event_type)
        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return

    if intent == "content":
        if reply:
            await _send_reply_for_event(message, reply, event_type=event_type)
        await _send_content(message, user_id=chat_id, ingest_hours_n=None)
        return

    if intent == "web_search":
        if reply:
            await _send_reply_for_event(message, reply, event_type=event_type)

        q = (getattr(decision, "query", "") or "").strip()
        if not q:
            q = _extract_web_search_query(text)

        await _run_web_search_for_message(message, q)
        return

    if reply:
        await _send_reply_for_event(message, reply, event_type=event_type)

@dp.message(F.voice)
async def on_voice(message: Message) -> None:
    if not _chat_allowed(message):
        return

    if _relay_is_armed(message):
        return

    if message.chat.type in ("group", "supergroup"):
        # В группе voice не должен включать Весю без reply на бота.
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
        )
        if not is_reply_to_bot:
            return

    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        raw = await _download_tg_file_bytes(message.bot, message.voice.file_id)
        text = await asyncio.to_thread(_transcribe_voice_ogg, raw)

        if not text:
            await message.answer("голос не разобрала.")
            return

        print(f"[voice] transcribed={text!r}", flush=True)

        await _handle_text_core(message, text, event_type="voice")

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
    # === SAVE PRIVATE USERS ===
    if message.chat.type == "private" and message.from_user:
        users = _load_private_users()
        users.add(int(message.from_user.id))
        _save_private_users(users)

    text = (message.text or "").strip()
    orig_text = text

    # Если это сообщение с медиа (фото/видео/документ) — НЕ обрабатываем как текст
    if message.photo or message.video or message.document:
        return

    # Пересланный текст сам по себе НЕ является вопросом к Весе.
    # Его комментируем только если пользователь явно спросил в этом же сообщении,
    # а обычный forward без просьбы просто кладём в контекст и молчим.
    if _is_forwarded_message(message) and not _wants_context_comment(text):
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

    # In groups: react only when bot is addressed (name/command/reply)
    if message.chat.type in ("group", "supergroup"):
        t = text.lower()

        is_cmd = t.startswith("/")
        is_name = chatgpt_dialog.persona.is_addressed(t)
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
        )

        if not (is_cmd or is_name or is_reply_to_bot):
            return

        # optional: remove name prefix "Веся, ..."
        if is_name and not is_cmd:
            text = chatgpt_dialog.persona.strip_name_prefix(text).lstrip(" ,:.-").strip()
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

    dialog_text = text

    dialog_text = text

    # === REPLY CONTEXT FOR LLM ===
    if message.reply_to_message:
        r = message.reply_to_message

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
                f"Веся, сообщение от {current_author}. "
                f"Он отвечает на сообщение пользователя {replied_author}: "
                f"«{replied_text}». "
                f"В его текущем сообщении местоимения вроде 'он', 'его', 'ему' относятся к {replied_author}. "
                f"Текущий текст: «{text}»."
            )
        else:
            dialog_text = (
                f"Веся, сообщение от {current_author}. "
                f"Он отвечает на сообщение пользователя {replied_author}. "
                f"В его текущем сообщении местоимения вроде 'он', 'его', 'ему' относятся к {replied_author}. "
                f"Текущий текст: «{text}»."
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
            import re
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

            out = (
                f"📩 {item.get('subject')}\n"
                f"От: {item.get('from')}\n"
                f"Дата: {item.get('date')}\n\n"
                f"{full_text}"
            )

            await message.answer(out[:3900])

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
    # MANUAL YOUTUBE SEARCH
    # =========================
    import re

    def _extract_manual_youtube_query(text: str) -> str | None:
        t = (text or "").strip()
        t = re.sub(r"^\s*(веся|веслава|веська)\s*[,.:;-]?\s*", "", t, flags=re.I)
        tl = t.lower()

        triggers = [
            "найди мне клип",
            "найди клип",
            "найди видео",
            "найди на ютубе",
            "найди в ютубе",
            "поищи клип",
            "поищи видео",
        ]

        for tr in triggers:
            if tl.startswith(tr):
                q = t[len(tr):].strip(" ,.:;-")
                return q or None

        return None


    async def _youtube_manual_search(query: str) -> dict | None:
        import urllib.parse, urllib.request, json
        from openai import OpenAI

        api_key = (os.getenv("YT_API_KEY") or "").strip()
        if not api_key:
            print("[yt_manual] missing YT_API_KEY", flush=True)
            return None

        raw_query = (query or "").strip()
        clean_query = raw_query

        print(f"[yt_manual] raw_query={raw_query!r} clean_query={clean_query!r}", flush=True)

        params = urllib.parse.urlencode({
            "part": "snippet",
            "type": "video",
            "maxResults": "10",
            "q": clean_query,
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

        x = None
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

            x = item
            break

        if x is None:
            x = items[0]

        vid = (x.get("id") or {}).get("videoId")
        title = (x.get("snippet") or {}).get("title")

        if not vid:
            return None

        sentyt.add(vid)
        _save_sent(sentyt_path, sentyt, keep_last=200)

        return {
            "title": title,
            "url": f"https://www.youtube.com/watch?v={vid}",
        }

    yt_query = _extract_manual_youtube_query(text)
    if yt_query:
        await message.answer("Ща найду...")

        try:
            found = await _youtube_manual_search(yt_query)
            if not found:
                await message.answer("Не нашла.")
                return

            await message.answer(f"{found['title']}\n{found['url']}")
            return

        except Exception as e:
            print(f"[yt_manual] error: {e}", flush=True)
            await message.answer("Ошибка поиска.")
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

        # If we have image bytes → call vision describe
        if img_bytes is not None:
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

    topic = _get_topic(chat_id, user_id)
    if topic and _looks_like_topic_followup(text):
        dd = chatgpt_dialog.continue_topic_discussion(text, topic)
        if dd and (dd.reply or "").strip():
            await _answer_long(message, dd.reply)
            return

    if is_reply_to_bot_content:
        await _handle_text_core(message, f"Веся, {text}", event_type="text")
        return

    await _handle_text_core(message, dialog_text, event_type="text")
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
    await dp.start_polling(bot)

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

        def _walk(payload):
            if not payload:
                return ""

            mime = payload.get("mimeType", "")
            body = payload.get("body", {}) or {}
            data = body.get("data")

            if data and mime == "text/plain":
                try:
                    return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
                except Exception:
                    return ""

            for p in payload.get("parts", []) or []:
                if p.get("mimeType") == "text/plain":
                    got = _walk(p)
                    if got:
                        return got

            for p in payload.get("parts", []) or []:
                got = _walk(p)
                if got:
                    return got

            return ""

        full_text = _walk(detail.get("payload") or {}) or detail.get("snippet", "")

        translated = chatgpt_dialog.translate_to_ru(full_text)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Удалить", callback_data=f"gmail_del_id:{msg_id}"),
        ]])

        await cb.message.answer(
            f"📩 {item.get('subject')}\n"
            f"От: {item.get('from')}\n"
            f"Дата: {item.get('date')}\n\n"
            f"{translated[:3300]}",
            reply_markup=kb,
        )
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