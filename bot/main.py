import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)

from config import BOT_TOKEN, FEEDBACK_FILE
from ingest_runner import ingest_hours

# MEMES
from meme_ranker import rank_memes

# B VIDEO
from b_video_ranker import rank_b_videos


# ======================
# Paths / persistent
# ======================
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

RAW_DIR = DATA_DIR / "raw"
POSTED_FILE = DATA_DIR / "a_posted_master.tsv"
ADMIN_CHAT_FILE = DATA_DIR / "admin_chat_id.txt"

LAST_DAILY_MEMES_FILE = DATA_DIR / "last_daily_memes_run_utc.txt"

# 06:00 MSK = 03:00 UTC (MSK=UTC+3)
DAILY_UTC_HOUR = 3
DAILY_UTC_MIN = 0

DAILY_INGEST_HOURS = 24

# MEMES counts
DAILY_MEMES_N = 30
MANUAL_MEMES_N = 30

# B VIDEO counts
MANUAL_B_VIDEO_N = 2

# feed names (for posted tracking)
FEED_MEMES_DAILY = "feed_memes_daily"
FEED_MEMES_MANUAL = "feed_memes"
FEED_B_VIDEO = "feed_b_video"

# bootstrap feedback + posted
if not FEEDBACK_FILE:
    raise RuntimeError("FEEDBACK_FILE is empty in config.py")

if not Path(FEEDBACK_FILE).exists():
    Path(FEEDBACK_FILE).write_text("timestamp\tuser\titem\taction\n", encoding="utf-8")

if not POSTED_FILE.exists():
    POSTED_FILE.write_text("timestamp\tuser\titem\tfeed\n", encoding="utf-8")


# ======================
# Bot core
# ======================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def feedback_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍", callback_data=f"like:{item_id}"),
                InlineKeyboardButton(text="👎", callback_data=f"skip:{item_id}"),
                InlineKeyboardButton(text="🚫", callback_data=f"ban:{item_id}"),
            ]
        ]
    )


def write_posted(user_id: int, item_id: str, feed: str) -> None:
    ts = datetime.utcnow().isoformat()
    with POSTED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{user_id}\t{item_id}\t{feed}\n")


def load_admin_chat_id() -> int | None:
    if not ADMIN_CHAT_FILE.exists():
        return None
    s = ADMIN_CHAT_FILE.read_text(encoding="utf-8").strip()
    try:
        return int(s)
    except Exception:
        return None


def save_admin_chat_id(chat_id: int) -> None:
    ADMIN_CHAT_FILE.write_text(str(chat_id), encoding="utf-8")


def last_daily_memes_key() -> str | None:
    if not LAST_DAILY_MEMES_FILE.exists():
        return None
    return LAST_DAILY_MEMES_FILE.read_text(encoding="utf-8").strip() or None


def set_last_daily_memes_key(key: str) -> None:
    LAST_DAILY_MEMES_FILE.write_text(key, encoding="utf-8")


def next_run_utc(now: datetime) -> datetime:
    target = now.replace(hour=DAILY_UTC_HOUR, minute=DAILY_UTC_MIN, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


async def send_memes(chat_id: int, n: int, feed: str) -> int:
    items = rank_memes(user_id=chat_id, n=n, feed=feed)

    if not items:
        await bot.send_message(chat_id, "Нет новых мемов.")
        return 0

    sent = 0
    for it in items:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(it.abs_path),
                caption=f"{it.tg_date_iso}\n{it.src}",
                reply_markup=feedback_keyboard(it.item_id),
            )
            write_posted(chat_id, it.item_id, feed)
            sent += 1
        except Exception as e:
            await bot.send_message(chat_id, f"send error: {e}")

    return sent


async def send_b_videos(chat_id: int, n: int, feed: str) -> int:
    items = rank_b_videos(user_id=chat_id, n=n, feed=feed)

    if not items:
        await bot.send_message(chat_id, "Нет новых B-видео.")
        return 0

    sent = 0
    for it in items:
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(it.abs_path),
                caption=f"{it.tg_date_iso}\n{it.src}",
                reply_markup=feedback_keyboard(it.item_id),
            )
            write_posted(chat_id, it.item_id, feed)
            sent += 1
        except Exception as e:
            await bot.send_message(chat_id, f"send error: {e}")

    return sent


async def daily_memes_loop() -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            run_at = next_run_utc(now)
            await asyncio.sleep(max(1.0, (run_at - now).total_seconds()))

            run_key = run_at.strftime("%Y-%m-%d")  # UTC date for 03:00 UTC
            if last_daily_memes_key() == run_key:
                continue

            admin_chat_id = load_admin_chat_id()
            if not admin_chat_id:
                continue

            await bot.send_message(admin_chat_id, "⏳ Daily MEMES: ingest24 (06:00 МСК)…")
            await ingest_hours(DAILY_INGEST_HOURS)
            await bot.send_message(admin_chat_id, "✅ Daily MEMES: ingest done. Sending…")

            sent = await send_memes(admin_chat_id, DAILY_MEMES_N, FEED_MEMES_DAILY)
            await bot.send_message(admin_chat_id, f"✅ Daily MEMES: отправил {sent} мемов.")

            set_last_daily_memes_key(run_key)

        except Exception as e:
            try:
                admin_chat_id = load_admin_chat_id()
                if admin_chat_id:
                    await bot.send_message(admin_chat_id, f"⚠️ Daily MEMES loop error: {e}")
            except Exception:
                pass
            await asyncio.sleep(60)


# ======================
# Commands
# ======================
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "Бот работает.\n"
        "• /set_admin — куда слать daily\n"
        "• /feed_memes — мемы вручную\n"
        "• /feed_b_video — B-видео вручную\n"
        "• /ingest24 — ручной ingest\n"
    )


@dp.message(Command("set_admin"))
async def set_admin_handler(message: Message):
    save_admin_chat_id(message.chat.id)
    await message.answer(f"✅ Admin chat сохранён: {message.chat.id}\nDaily MEMES будет слать в 06:00 МСК.")


@dp.message(Command("ingest24"))
async def cmd_ingest24(message: Message):
    await message.answer("⏳ Запускаю ingest за 24 часа...")
    await ingest_hours(24)
    await message.answer("✅ Ingest за 24 часа завершён.")


@dp.message(Command("feed_memes"))
async def feed_memes_handler(message: Message):
    await message.answer("🖼️ Отправляю мемы...")
    sent = await send_memes(message.chat.id, MANUAL_MEMES_N, FEED_MEMES_MANUAL)
    await message.answer(f"Готово. Отправил {sent} мемов.")


@dp.message(Command("feed_b_video"))
async def feed_b_video_handler(message: Message):
    await message.answer("🔞 Отправляю B-видео...")
    sent = await send_b_videos(message.chat.id, MANUAL_B_VIDEO_N, FEED_B_VIDEO)
    await message.answer(f"Готово. Отправил {sent} B-видео.")


@dp.callback_query()
async def feedback_handler(callback: types.CallbackQuery):
    action, item_id = callback.data.split(":", 1)

    user = callback.from_user.id
    timestamp = datetime.utcnow().isoformat()

    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp}\t{user}\t{item_id}\t{action}\n")

    await callback.answer(f"Сохранено: {action}")


# ======================
# Main
# ======================
async def main():
    asyncio.create_task(daily_memes_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
