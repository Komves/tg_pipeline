# bot/main.py

import os
import asyncio
import random
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

import persona
import memory
import news_digest
import c_youtube_fetcher
import ingest_runner
import nsfw_runner
import ranker


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

MSK = ZoneInfo("Europe/Moscow")

router = Router()

_run_lock = asyncio.Lock()

HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))

NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

NEWS_SOURCES_FILE = Path(__file__).parent / "news_sources.txt"


def log(msg):
    print("[main]", msg, flush=True)


# ======================
# NEWS
# ======================

async def run_news():

    async with _run_lock:

        bot = Bot(token=BOT_TOKEN)

        try:

            items = await news_digest.get_news_digest(
                news_sources_path=NEWS_SOURCES_FILE,
                hours=NEWS_HOURS,
                limit=NEWS_LIMIT,
            )

            html = news_digest.build_html_message(items, hours=NEWS_HOURS)

            await bot.send_message(
                chat_id=os.getenv("CHAT_ID"),
                text=html,
                parse_mode="HTML",
            )

        finally:

            await bot.session.close()


@router.message(Command("news"))
async def news_cmd(msg: Message):

    await msg.answer("собираю")

    asyncio.create_task(run_news())


# ======================
# VESYA CHAT FIX
# ======================

async def delayed_answer(msg, text, delay):

    await asyncio.sleep(delay)

    try:

        await msg.answer(text)

    except Exception:

        pass


@router.message()
async def vesya_handler(msg: Message):

    text = (msg.text or "").strip()

    if not text:

        return

    if text.startswith("/"):

        return

    ir = persona.detect_intent(text)

    if not ir.addressed:

        return

    intent = ir.intent

    # ===== ping =====

    if intent == "ping":

        delay = persona.maybe_delay_seconds_for_ping()

        if delay:

            asyncio.create_task(
                delayed_answer(
                    msg,
                    f"{persona.ping_answer()}. {persona.excuse_text()}",
                    delay,
                )
            )

        else:

            await msg.answer(persona.ping_answer())

        return

    # ===== CHAT FIX (главное) =====

    if intent == "chat":

        reply = persona.answer_info_fast(ir.question or text)

        await msg.answer(reply)

        return

    # ===== bot question =====

    if intent == "bot_q":

        await msg.answer(persona.bot_q_answer())

        return

    # ===== news =====

    if intent == "news":

        await msg.answer("смотрю")

        asyncio.create_task(run_news())

        return


# ======================
# LOOP
# ======================

async def scheduler_loop():

    while True:

        log("heartbeat")

        await asyncio.sleep(HEARTBEAT_SEC)


async def main_async():

    log("worker started")

    bot = Bot(token=BOT_TOKEN)

    dp = Dispatcher()

    dp.include_router(router)

    await asyncio.gather(

        dp.start_polling(bot),

        scheduler_loop(),

    )


def main():

    asyncio.run(main_async())


if __name__ == "__main__":

    main()
