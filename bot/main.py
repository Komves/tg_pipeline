# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations
import asyncio
import os
import time
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
RECENT_MSG_IDS = {}
FEEDBACK_PATH = "/data/feedback.tsv"
TG_LOCK = asyncio.Lock()
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

import chatgpt_dialog
import news_digest

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
    await message.answer("ок. контент пайплайн сейчас не подключён в этом main.py.")


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

    
    if intent == "news":
        from aiogram.enums import ChatAction
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("ок. сейчас соберу сводку.")

        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return


    if intent == "content":
        from aiogram.enums import ChatAction
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if reply:
            await message.answer(reply)
        else:
            await message.answer("сек, собираю горячее.")
        # pipeline (content) — ТОЛЬКО внутри intent == "content"
        from ranker import rank_top_n, CAT_A_VIDEO
        from ingest_runner import ingest_hours
        from meme_ranker import rank_memes
        from aiogram.types import FSInputFile
        print("[content] calling ingest_hours(12)...", flush=True)

        try:
            await ingest_hours(12)
        except Exception as e:
            print(f"[content] ingest_hours error: {e}", flush=True)

        print("[content] ingest_hours done", flush=True)

# видео
        a_items = rank_top_n(
            user_id=user_id,
            category=CAT_A_VIDEO,
            n=3,
        )

        for it in a_items:
            await message.answer_video(
                FSInputFile(it.abs_path),
                reply_markup=fb_kb(it.item_id),
            )
# мемы
        m_items = rank_memes(user_id=user_id, n=3)

        for it in m_items:
            await message.answer_photo(
                FSInputFile(it.abs_path),
                reply_markup=fb_kb(it.item_id),
            )
        return

    

# =========================
# HEARTBEAT (optional)
# =========================
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

    await cb.answer("принято 🔥")


if __name__ == "__main__":
    asyncio.run(main())
