# bot/main.py (aiogram canonical sender; Telethon ingest-only via modules)
from __future__ import annotations

import asyncio
import os
import time
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
    if not _chat_allowed(message):
        return

    text = (message.text or "").strip()
    if not text:
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0

    decision = chatgpt_dialog.decide(chat_id, user_id, text)
    intent = (decision.intent or "chat").strip().lower()
    reply = (decision.reply or "").strip()

    if intent == "news":
        # reply должен быть “человеческим” (guardrail в chatgpt_dialog.py)
        if reply:
            await message.answer(reply)
        else:
            await message.answer("ок. сейчас соберу сводку.")
        await _run_news_for_message(message, hours=DEFAULT_NEWS_HOURS, limit=DEFAULT_NEWS_LIMIT)
        return

    if intent == "content":
        if reply:
            await message.answer(reply)
        else:
            await message.answer("ок. сейчас соберу контент.")
        # безопасный stub
        await message.answer("контент пайплайн сейчас не подключён в этом main.py.")
        return

    if intent == "end":
        await message.answer(reply or "принято.")
        return

    # обычный чат
    if reply:
        await message.answer(reply)


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


if __name__ == "__main__":
    asyncio.run(main())
