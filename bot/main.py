# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations
import asyncio
import os
import time
import random
import json
import shutil
import uuid
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
# last image per (chat_id, user_id) to support "опиши фото" without reply
LAST_USER_IMAGE_ID = {}  # (chat_id:int, user_id:int) -> file_id:str

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

import json

def _load_sent(path: Path) -> set[str]:
    try:
        if path.exists():
            return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()

def _save_sent(path: Path, sent: set[str], *, keep_last: int = 500) -> None:
    try:
        data = list(sent)
        if len(data) > keep_last:
            data = data[-keep_last:]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

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
import c_youtube_fetcher

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes


# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty (set Render env var BOT_TOKEN).")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
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

NEWS_SOURCES = Path("news_sources.txt")  # лежит рядом с main.py в /opt/render/project/src/bot

DEFAULT_NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
DEFAULT_NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

# optional: restrict to one chat
_CHAT_ID_ENV = (os.getenv("CHAT_ID") or "").strip()
ALLOWED_CHAT_ID: Optional[int] = int(_CHAT_ID_ENV) if _CHAT_ID_ENV else None

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


# =========================
# NEWS RUNNER (calls Telethon inside news_digest)
# =========================
async def _run_news_for_message(message: Message, *, hours: int, limit: int) -> None:
    async with TG_LOCK:
        items = await news_digest.get_news_digest(
            news_sources_path=NEWS_SOURCES,
            hours=hours,
            limit=limit,
        )

        text = news_digest.build_html_message(items, hours=hours)
        await message.answer(text, parse_mode="html")
        news_digest.mark_digest_as_seen(items)

# =========================
# COMMANDS
# =========================
@dp.message(Command("news"))
async def cmd_news(message: Message) -> None:
    if not _chat_allowed(message):
        return
    await message.answer("ок. сейчас соберу сводку.")
    await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)

@dp.message(Command("get12"))

async def cmd_get12(message: Message) -> None:
    if not _chat_allowed(message):
        return
    # у тебя content pipeline может быть в другом модуле; тут безопасный stub
    await vesya_handler(message)

# =========================
# MAIN ROUTER
# =========================
async def _send_content(message: Message, *, user_id: int, ingest_hours_n: int | None) -> None:
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

    # --- videos ---
    sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
    sentv = _load_sent(sentv_path)

    a_items = rank_top_n(user_id=user_id, category=CAT_A_VIDEO, n=3)
    a_items = [it for it in a_items if it.item_id not in sentv]

    seen = set()
    a_items = [it for it in a_items if not (it.item_id in seen or seen.add(it.item_id))]

    for it in a_items:
        tmp_path = f"/tmp/vesya_video_{uuid.uuid4().hex}.mp4"
        try:
            shutil.copyfile(it.abs_path, tmp_path)
            await message.answer_video(
                FSInputFile(tmp_path),
                reply_markup=fb_kb(it.item_id),
            )
            sentv.add(it.item_id)
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    _save_sent(sentv_path, sentv, keep_last=700)

    # --- мемы (GPT BATCH RANKING: Variant A) ---
    sentm_path = DATA_DIR / f"sent_meme_{user_id}.json"
    sentm = _load_sent(sentm_path)

    POOL_N = int(os.getenv("V_MEME_POOL_N", "30"))
    SEND_K = int(os.getenv("V_MEME_SEND_K", "6"))

    m_items = rank_memes(user_id=user_id, n=POOL_N)
    m_items = [it for it in m_items if it.item_id not in sentm]

    print(f"[meme] pool_after_sent={len(m_items)} pool_n={POOL_N}", flush=True)

    batch: list[chatgpt_dialog.MemeCandidate] = []
    for it in m_items:
        try:
            p = Path(it.abs_path)
            if not p.exists():
                continue

            img_bytes = p.read_bytes()

            cap = ""
            src = getattr(it, "src", "") or ""

            mp = Path(str(p) + ".meta.json")
            if mp.exists():
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                    cap = (meta.get("caption") or "").strip()
                    if not src:
                        src = (meta.get("src") or "").strip()
                except Exception:
                    pass

            batch.append(
                chatgpt_dialog.MemeCandidate(
                    item_id=str(it.item_id),
                    img_bytes=img_bytes,
                    caption=cap,
                    src=src,
                )
            )
        except Exception:
            continue

    picked_ids: list[str] = []
    try:
        res = chatgpt_dialog.meme_rank_batch(batch, top_k=SEND_K)
        picked_ids = list(res.get("picked_item_ids") or [])
    except Exception as e:
        print(f"[meme] meme_rank_batch error: {type(e).__name__}: {e}", flush=True)
        picked_ids = []

    picked_set = set(picked_ids)
    picked_items = [it for it in m_items if it.item_id in picked_set]

    # preserve GPT order
    order = {iid: i for i, iid in enumerate(picked_ids)}
    picked_items.sort(key=lambda it: order.get(it.item_id, 10**9))

    print(f"[meme] picked={len(picked_items)} send_k={SEND_K}", flush=True)

    for it in picked_items[:SEND_K]:
        try:
            await message.answer_photo(
                FSInputFile(it.abs_path),
                reply_markup=fb_kb(it.item_id),
            )
            sentm.add(it.item_id)
        except Exception as e:
            print(
                f"[meme] send error item_id={it.item_id} path={it.abs_path} err={type(e).__name__}: {e}",
                flush=True,
            )

    _save_sent(sentm_path, sentm, keep_last=700)
    
    # --- youtube (оставь твой текущий блок здесь как есть) ---
    # !!! ВАЖНО: просто перенеси сюда весь текущий try/except youtube из vesya_handler
    # и НЕ вызывай здесь ingest_hours.
# =========================
# IMAGE HANDLERS (group vision react)
# =========================

async def _download_tg_file_bytes(file_id: str) -> bytes:
    f = await bot.get_file(file_id)
    bio = io.BytesIO()
    await bot.download_file(f.file_path, destination=bio)
    return bio.getvalue()


@dp.message(F.photo)
async def on_photo(message: Message) -> None:
    print("[IMG] photo handler triggered", flush=True)

    if not _chat_allowed(message):
        return
        
    try:
        ph = message.photo[-1]
                # save last photo id even if limiter skips reactions
        uid = int(message.from_user.id) if message.from_user else 0
        LAST_USER_IMAGE_ID[(int(message.chat.id), uid)] = ph.file_id

        if not _img_should_react(int(message.chat.id)):
            print("[IMG] skipped by limiter (but saved last file_id)", flush=True)
            return

        raw = await _download_tg_file_bytes(ph.file_id)

        img_bytes = _shrink_jpeg_bytes(raw)
        
        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )
                
        fn = IMG_INBOX / f"{message.chat.id}_{message.message_id}.jpg"

        try:
            fn.write_bytes(img_bytes)
        except Exception:
            pass

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

        if action == "like":
            await message.reply(reply or "🔥")

        elif action == "comment":
            await message.reply(reply or "норм")

    except Exception as e:
        print(f"[img] photo error: {e}", flush=True)



@dp.message(F.document)
async def on_image_document(message: Message) -> None:

    if not _chat_allowed(message):
        return
    if not _img_should_react(int(message.chat.id)):
        print("[IMG] doc skipped by limiter", flush=True)
        return
    try:
        doc = message.document
    
        if not doc.mime_type.startswith("image/"):
            return

        raw = await _download_tg_file_bytes(doc.file_id)

        img_bytes = _shrink_jpeg_bytes(raw)
                # save last image-document for "опиши фото" without reply
        chatgpt_dialog.note_last_user_photo(
            int(message.chat.id),
            int(message.from_user.id) if message.from_user else 0,
            img_bytes,
        )

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

        if action == "like":
            await message.reply(reply or "👍")

        elif action == "comment":
            await message.reply(reply or "ок")

    except Exception as e:
        print(f"[img] doc error: {e}", flush=True)

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
        # =========================
    print(f"[route] text={text!r}", flush=True)
    if not text:
        return

    # =========================
    # PHOTO CONTEXT: reply OR last saved photo
    # =========================
    try:
        img_bytes = None

        # 1) If user replied to a photo/document → use that
        r = message.reply_to_message
        if r:
            img_id = None
            if getattr(r, "photo", None):
                img_id = r.photo[-1].file_id
            elif getattr(r, "document", None) and (getattr(r.document, "mime_type", "") or "").startswith("image/"):
                img_id = r.document.file_id

            if img_id:
                raw = await _download_tg_file_bytes(img_id)
                img_bytes = _shrink_jpeg_bytes(raw)

        # 2) If not reply, but text asks to describe → use last saved photo
        wants_photo = False
        if img_bytes is None:
            t = text.lower()
            wants_photo = (
                "что на фото" in t
                or "что ты видишь" in t
                or t.startswith("опиши")
                or "опиши фото" in t
                or "опиши форт" in t
                or "опиши" in t
            )
        if wants_photo:
            uid = int(message.from_user.id) if message.from_user else 0
            fid = LAST_USER_IMAGE_ID.get((int(message.chat.id), uid))
            if fid:
                raw = await _download_tg_file_bytes(fid)
                img_bytes = _shrink_jpeg_bytes(raw)

        # 3) If we have image bytes → call vision describe
        if img_bytes is not None:
            dd = chatgpt_dialog.describe_or_compare_photo(text, img_bytes)
            if dd and (dd.reply or "").strip():
                await message.answer(dd.reply)
                return

    except Exception as e:
        print(f"[IMG] photo describe routing error: {type(e).__name__}: {e}", flush=True)
    
    
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    reply = ""
    intent = "chat"
    decision = chatgpt_dialog.decide(chat_id, user_id, text)
    print(f"[route] intent={decision.intent} reply={decision.reply!r}", flush=True)
    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()

    if intent == "chat":
        # имитация "печатает..." + пауза 5–10 секунд
        wait_s = random.uniform(5, 10)

        # Telegram "typing" живёт недолго, поэтому поддерживаем его до отправки
        end_at = time.time() + wait_s
        while time.time() < end_at:
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            await asyncio.sleep(min(4.0, end_at - time.time()))

        await message.answer(reply or "слушаю")
        return
    
    if intent == "news":
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("ок. сейчас соберу сводку.")

        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return


    if intent == "content":
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("сек, собираю горячее.")
            print("[content] calling ingest_hours(12)...", flush=True)

        # --- Telethon/sqlite только под lock ---
        async with TG_LOCK:
            try:
                await ingest_hours(12)
            except Exception as e:
                print(f"[content] ingest_hours error: {e}", flush=True)

        # --- кнопки фидбека ---
        def fb_kb(item_id: str) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
                InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
                InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
            ]])

        # --- видео ---
        sentv_path = DATA_DIR / f"sent_video_{user_id}.json"
        sentv = _load_sent(sentv_path)

        a_items = rank_top_n(user_id=user_id, category=CAT_A_VIDEO, n=3)
        a_items = [it for it in a_items if it.item_id not in sentv]

        seen = set()
        a_items = [it for it in a_items if not (it.item_id in seen or seen.add(it.item_id))]

        for it in a_items:
            tmp_path = f"/tmp/vesya_video_{uuid.uuid4().hex}.mp4"
            try:
                shutil.copyfile(it.abs_path, tmp_path)
                await message.answer_video(
                    FSInputFile(tmp_path),
                    reply_markup=fb_kb(it.item_id),
                )
                sentv.add(it.item_id)
            finally:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        _save_sent(sentv_path, sentv, keep_last=700)

        # --- мемы ---
        sentm_path = DATA_DIR / f"sent_meme_{user_id}.json"
        sentm = _load_sent(sentm_path)

        m_items = rank_memes(user_id=user_id, n=10)
        m_items = [it for it in m_items if it.item_id not in sentm]

        m_ok = []
        for it in m_items:
            ok = await _gpt_meme_ok(it.abs_path, src=getattr(it, "src", "") or "")
            if ok:
                m_ok.append(it)
        m_items = m_ok

        for it in m_items:
            await message.answer_photo(
                FSInputFile(it.abs_path),
                reply_markup=fb_kb(it.item_id),
            )
            sentm.add(it.item_id)

        _save_sent(sentm_path, sentm, keep_last=700)

         # --- youtube links (MIX pool: 6 links) ---
        try:
            def yt_kb(item_id: str) -> InlineKeyboardMarkup:
                return InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
                    InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
                    InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
                ]])

            sentyt_path = DATA_DIR / f"sent_yt_{user_id}.json"
            sentyt = _load_sent(sentyt_path)

            ytstate_path = DATA_DIR / f"yt_state_{user_id}.json"
            try:
                _st = json.loads(ytstate_path.read_text(encoding="utf-8")) if ytstate_path.exists() else {}
            except Exception:
                _st = {}

            posted_ids = set(_st.get("posted_video_ids") or [])
            last_sent_by_source = dict(_st.get("last_sent_by_source") or {})

            pool = c_youtube_fetcher.get_batch(
                limit=6,
                posted_video_ids=posted_ids,
                last_sent_by_source=last_sent_by_source,
                mode="mix",
            )

            final: list[tuple[str, str, str]] = []  # (title, url, vid)
            for x in pool:
                title = (x.get("title") or "").strip()
                url = (x.get("url") or "").strip()
                vid = (x.get("video_id") or "").strip()
                if not url or (url in sentyt):
                    continue
                item_id = f"yt:{url}"
                if _is_banned(item_id):
                    continue
                final.append((title, url, vid))
                if len(final) >= 6:
                    break

            for (title, url, vid) in final:
                item_id = f"yt:{url}"
                text2 = f"🎵 {title}\n{url}" if title else url
                await message.answer(text2, reply_markup=yt_kb(item_id))

                sentyt.add(url)
                if vid:
                    posted_ids.add(vid)
                    last_sent_by_source["c_youtube"] = vid

            _save_sent(sentyt_path, sentyt, keep_last=800)

            try:
                ytstate_path.write_text(
                    json.dumps(
                        {
                            "posted_video_ids": list(posted_ids)[-5000:],
                            "last_sent_by_source": last_sent_by_source,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

        except Exception as e:
            print(f"[content] youtube links error: {type(e).__name__}: {e}", flush=True)

        except Exception as e:
            print(f"[content] youtube links error: {type(e).__name__}: {e}", flush=True)

        return

# =====================
# INGEST24 LOOP (06:00 MSK)
# =====================

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

async def ingest24_loop(bot: Bot) -> None:
    await asyncio.sleep(10)

    while True:
        now = datetime.now(MSK)
        next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)

        if now >= next_run:
            next_run += timedelta(days=1)

        wait_sec = (next_run - now).total_seconds()
        print(f"[ingest24] next run in {int(wait_sec)} sec", flush=True)

        await asyncio.sleep(wait_sec)

        print("[ingest24] starting ingest_hours(24)", flush=True)

        try:
            async with TG_LOCK:
                await ingest_hours(24)

                       # отправка пользователю
            if not RECENT_MSG_IDS:
                # last image per (chat_id, user_id) to support "опиши фото" without reply
                LAST_USER_IMAGE_ID = {}  # (chat_id:int, user_id:int) -> file_id:str

                print("[ingest24] no recent users, skip sending", flush=True)
                continue

            chat_id = list(RECENT_MSG_IDS.keys())[-1][0]
            user_id = list(RECENT_MSG_IDS.keys())[-1][1]

            class Dummy:
                def __init__(self, bot, chat_id, user_id):
                    self.bot = bot
                    self.chat = type("c", (), {"id": chat_id})
                    self.message_id = 0
                    self.from_user = type("u", (), {"id": user_id})

                async def answer(self, text, **kw):
                    return await self.bot.send_message(self.chat.id, text, **kw)

                async def answer_photo(self, photo, **kw):
                    return await self.bot.send_photo(self.chat.id, photo, **kw)

                async def answer_video(self, video, **kw):
                    return await self.bot.send_video(self.chat.id, video, **kw)

                async def answer_document(self, document, **kw):
                    return await self.bot.send_document(self.chat.id, document, **kw)

            dummy = Dummy(bot, chat_id, user_id)

            await _send_content(dummy, user_id=user_id, ingest_hours_n=None)

        except Exception as e:
            print(f"[ingest24] error: {e}", flush=True)
async def heartbeat_loop() -> None:
    while True:
        _log("heartbeat")
        await asyncio.sleep(300)

# =========================
# START
# =========================
async def main() -> None:
    _log("starting aiogram polling")
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(ingest24_loop(bot))
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



if __name__ == "__main__":
    asyncio.run(main())
