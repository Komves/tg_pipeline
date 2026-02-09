# BOT/main.py
import os
import json
import asyncio
import hashlib
import random
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import ingest_runner
import nsfw_runner
import ranker
import c_youtube_fetcher
import news_digest
import memory
import persona

# NEW — диалог через ChatGPT
import dialog_engine

try:
    import meme_ranker
except Exception:
    meme_ranker = None

try:
    import b_video_ranker
except Exception:
    b_video_ranker = None


BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR = DATA_DIR / "raw"

POSTED_TSV = DATA_DIR / "a_posted_master.tsv"
FEEDBACK_TSV = DATA_DIR / "feedback.tsv"
SENT_INDEX_JSON = DATA_DIR / "sent_index.json"
STATE_PATH = DATA_DIR / "daily_state.json"

C_POSTED_TSV = DATA_DIR / "c_posted_master.tsv"

A_MEMES_LIMIT = int(os.getenv("A_MEMES_LIMIT", "30"))
A_VIDEOS_LIMIT = int(os.getenv("A_VIDEOS_LIMIT", "20"))
B_VIDEOS_LIMIT = int(os.getenv("B_VIDEOS_LIMIT", "5"))

HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))

MSK = ZoneInfo("Europe/Moscow")
AUTO_DEADLINE_MSK = dtime(6, 0, 0)

NEWS_HOURS = int(os.getenv("NEWS_HOURS", "12"))
NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "10"))

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
NEWS_SOURCES_PRIMARY = _THIS_DIR / "news_sources.txt"
NEWS_SOURCES_FALLBACK = _REPO_ROOT / "tg_pipeline" / "news_sources.txt"
NEWS_SOURCES_FILE = NEWS_SOURCES_PRIMARY if NEWS_SOURCES_PRIMARY.exists() else NEWS_SOURCES_FALLBACK

_run_lock = asyncio.Lock()
router = Router()


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


def _resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID
    if ADMIN_USER_IDS:
        s = ADMIN_USER_IDS.replace(",", " ").split()
        if s:
            return s[0].strip()
    return ""


# =========================
# PIPELINES
# =========================

async def run_all(hours: int, *, reason: str) -> None:

    async with _run_lock:

        log(f"RUN start reason={reason}")

        try:
            await ingest_runner.ingest_hours(hours)
        except Exception as e:
            log(f"ingest error: {e}")

        try:
            nsfw_runner.score_missing_b(hours=hours)
        except Exception as e:
            log(f"nsfw error: {e}")

        a_memes = []
        a_videos = []
        b_videos = []

        if meme_ranker:
            try:
                a_memes = meme_ranker.rank_memes(0, A_MEMES_LIMIT)
            except Exception:
                pass

        try:
            a_videos = ranker.rank_top_n(
                user_id=0,
                category=ranker.CAT_A_VIDEO,
                n=A_VIDEOS_LIMIT,
                feed="feed_a_video"
            )
        except Exception:
            pass

        if b_video_ranker:
            try:
                b_videos = b_video_ranker.rank_b_videos(0, B_VIDEOS_LIMIT)
            except Exception:
                pass

        try:
            c_items = c_youtube_fetcher.get_batch(limit=2)
        except Exception:
            c_items = []

        items = list(a_memes) + list(a_videos) + list(b_videos) + list(c_items)

        bot = Bot(token=BOT_TOKEN)

        try:
            for it in items:

                if isinstance(it, dict) and it.get("url"):
                    await bot.send_message(_resolve_chat_id(), it["url"])
                    continue

                path = getattr(it, "abs_path", None) or it.get("abs_path")
                if not path:
                    continue

                file = FSInputFile(path)

                if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    await bot.send_photo(_resolve_chat_id(), file)

                elif path.lower().endswith((".mp4", ".mov", ".webm")):
                    await bot.send_video(_resolve_chat_id(), file)

                else:
                    await bot.send_document(_resolve_chat_id(), file)

        finally:
            await bot.session.close()

        log("RUN end")


async def run_news(*, hours: int, limit: int, reason: str):

    async with _run_lock:

        log(f"NEWS start reason={reason}")

        bot = Bot(token=BOT_TOKEN)

        try:

            items = await news_digest.get_news_digest(
                news_sources_path=NEWS_SOURCES_FILE,
                hours=hours,
                limit=limit,
            )

            html = news_digest.build_html_message(items, hours)

            await bot.send_message(
                _resolve_chat_id(),
                html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        finally:
            await bot.session.close()

        log("NEWS end")


# =========================
# COMMANDS
# =========================

@router.message(Command("get12"))
async def cmd_get12(msg: Message):
    await msg.answer("сек.")
    asyncio.create_task(run_all(12, reason="manual"))


@router.message(Command("news"))
async def cmd_news(msg: Message):
    await msg.answer("собираю.")
    asyncio.create_task(run_news(hours=NEWS_HOURS, limit=NEWS_LIMIT, reason="manual"))


# =========================
# CHATGPT-DRIVEN DIALOG
# =========================

@router.message()
async def vesya_handler(msg: Message):

    text = (msg.text or "").strip()

    if not text:
        return

    if text.startswith("/"):
        return

    if not persona.is_addressed(text):
        return

    try:

        decision = dialog_engine.decide(text)

        intent = decision.get("intent", "chat")
        reply = decision.get("reply", "")

        if reply:
            await msg.bot.send_chat_action(msg.chat.id, "typing")
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await msg.answer(reply)

        if intent == "run_all":
            asyncio.create_task(run_all(12, reason="chatgpt"))

        elif intent == "news":
            asyncio.create_task(run_news(
                hours=NEWS_HOURS,
                limit=NEWS_LIMIT,
                reason="chatgpt"
            ))

    except Exception as e:

        log(f"dialog error: {e}")


# =========================
# SCHEDULER
# =========================

async def scheduler_loop():

    while True:

        try:

            now = datetime.now(MSK)

            if now.time() <= AUTO_DEADLINE_MSK:

                state = {}

                if STATE_PATH.exists():
                    state = json.loads(STATE_PATH.read_text())

                if state.get("day") != now.strftime("%Y-%m-%d"):

                    asyncio.create_task(run_all(24, reason="auto"))

                    state["day"] = now.strftime("%Y-%m-%d")

                    STATE_PATH.write_text(json.dumps(state))

        except Exception as e:

            log(f"scheduler error: {e}")

        await asyncio.sleep(HEARTBEAT_SEC)


# =========================
# MAIN
# =========================

async def main_async():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    log("started")

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
