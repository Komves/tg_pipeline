# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations
import asyncio
import os
import time
import json
import shutil
import uuid
from pathlib import Path
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile

from ranker import rank_top_n, CAT_A_VIDEO
from ingest_runner import ingest_hours
from meme_ranker import rank_memes

import c_youtube_fetcher

RECENT_MSG_IDS = {}
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
        return True

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
    print(f"[route] text={text!r}", flush=True)
    if not text:
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    reply = ""
    intent = "chat"
    decision = chatgpt_dialog.decide(chat_id, user_id, text)
    print(f"[route] intent={decision.intent} reply={decision.reply!r}", flush=True)
    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()
    if intent == "chat":
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

        async with TG_LOCK:
            try:
                await ingest_hours(12)
            except Exception as e:
                print(f"[content] ingest_hours error: {e}", flush=True)

    # всё ниже — ТОЛЬКО внутри: if intent == "content":

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

                    # --- youtube links (6: 2 EN / 2 RU / 2 AI) ---
        try:
            def yt_kb(item_id: str) -> InlineKeyboardMarkup:
                return InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="👍", callback_data=f"fb:up:{item_id}"),
                    InlineKeyboardButton(text="👎", callback_data=f"fb:down:{item_id}"),
                    InlineKeyboardButton(text="🚫 BAN", callback_data=f"fb:ban:{item_id}"),
                ]])

            # --- yt state (persisted) ---
            ytstate_path = DATA_DIR / f"yt_state_{user_id}.json"
            try:
                _st = json.loads(ytstate_path.read_text(encoding="utf-8")) if ytstate_path.exists() else {}
            except Exception:
                _st = {}
            posted_ids = set(_st.get("posted_video_ids") or [])
            last_sent_by_source = dict(_st.get("last_sent_by_source") or {})

            # Берём с запасом, чтобы отфильтровать недоступные/неподходящие
            pool = await asyncio.wait_for(
                asyncio.to_thread(
                    c_youtube_fetcher.get_batch,
                    limit=30,
                    posted_video_ids=posted_ids,
                    last_sent_by_source=last_sent_by_source,
                ),
                timeout=120,
)

            # url -> video_id (для posted_ids)
            url_to_vid = {}
            for _x in pool:
                _u = (_x.get("url") or "").strip()
                _v = (_x.get("video_id") or "").strip()
                if _u and _v:
                    url_to_vid[_u] = _v

            def norm(s: str) -> str:
                return (s or "").strip().lower()

            def is_ai(title: str, desc: str, uploader: str) -> bool:
                t = norm(title); d = norm(desc); u = norm(uploader)
                keys = ["ai cover", "a.i. cover", "ai кавер", "аi кавер", "нейрокавер", "нейро кавер", "нейро-кавер", "voice model", "rvc"]
                return any((k in t) or (k in d) or (k in u) for k in keys)

            def is_ru(title: str, desc: str, uploader: str) -> bool:
                text = (title or "") + " " + (desc or "") + " " + (uploader or "")
                return any(("а" <= ch.lower() <= "я") or (ch.lower() == "ё") for ch in text)

            sentyt_path = DATA_DIR / f"sent_yt_{user_id}.json"
            try:
                sentyt = set(json.loads(sentyt_path.read_text(encoding="utf-8"))) if sentyt_path.exists() else set()
            except Exception:
                sentyt = set()

            picked_en = []
            picked_ru = []
            picked_ai = []
            seen_urls = set()

            # 1) набираем 2/2/2, бан применяем НА ОТБОРЕ
            for x in pool:
                title = (x.get("title") or "").strip()
                url = (x.get("url") or "").strip()
                uploader = (x.get("uploader") or x.get("channel") or "").strip()
                desc = (x.get("description") or "").strip()

                if (not url) or (not url.startswith("http")):
                    continue
                if url in seen_urls or url in sentyt:
                    continue
                if _is_banned(f"yt:{url}"):
                    continue

                ai = is_ai(title, desc, uploader)
                ru = is_ru(title, desc, uploader)

                if ai and len(picked_ai) < 2:
                    picked_ai.append((title, url))
                    seen_urls.add(url)
                    continue

                if ru and len(picked_ru) < 2:
                    picked_ru.append((title, url))
                    seen_urls.add(url)
                    continue

                if (not ru) and len(picked_en) < 2:
                    picked_en.append((title, url))
                    seen_urls.add(url)
                    continue

                if len(picked_ai) == 2 and len(picked_ru) == 2 and len(picked_en) == 2:
                    break

            final = picked_en + picked_ru + picked_ai

            # 2) добор до 6 (тоже с баном/антиповтором)
            if len(final) < 6:
                for x in pool:
                    title = (x.get("title") or "").strip()
                    url = (x.get("url") or "").strip()

                    if (not url) or (not url.startswith("http")):
                        continue
                    if url in seen_urls or url in sentyt:
                        continue
                    if _is_banned(f"yt:{url}"):
                        continue

                    final.append((title, url))
                    seen_urls.add(url)

                    if len(final) == 6:
                        break

            final = final[:6]

            # 3) отправка + обновление sent/state
            for (title, url) in final:
                item_id = f"yt:{url}"
                text = f"🎵 {title}\n{url}" if title else url
                for _attempt in range(3):
                    try:
                        await message.answer(text, reply_markup=yt_kb(item_id))
                        break
                    except Exception as _e:
                        if _attempt == 2:
                            raise
                        await asyncio.sleep(1.5)

                sentyt.add(url)

                vid = url_to_vid.get(url)
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

        return

# =========================
# HEARTBEAT (optional)
# =========================
async def heartbeat_loop() -> None:
    while True:
        _log("heartbeat")
        await asyncio.sleep(300)

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

            # отправка пользователю (тот же user_id что использовался последним)
            if RECENT_MSG_IDS:
                chat_id = list(RECENT_MSG_IDS.keys())[-1][0]

                class Dummy:
                    def __init__(self, bot, chat_id):
                        self.bot = bot
                        self.chat = type("c", (), {"id": chat_id})

                    async def answer(self, text, **kw):
                        await self.bot.send_message(self.chat.id, text, **kw)

                    async def answer_photo(self, photo, **kw):
                        await self.bot.send_photo(self.chat.id, photo, **kw)

                    async def answer_video(self, video, **kw):
                        await self.bot.send_video(self.chat.id, video, **kw)

                dummy = Dummy(bot, chat_id)

                # используем существующий pipeline content
                await vesya_handler(dummy)

        except Exception as e:
            print(f"[ingest24] error: {e}", flush=True)

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
