import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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


# =========================
# Persistent files (Render)
# =========================
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

POSTED_FILE = DATA_DIR / "a_posted_master.tsv"
ADMIN_CHAT_FILE = DATA_DIR / "admin_chat_id.txt"

if not Path(FEEDBACK_FILE).exists():
    Path(FEEDBACK_FILE).write_text("timestamp\tuser\titem\taction\n", encoding="utf-8")

if not POSTED_FILE.exists():
    POSTED_FILE.write_text("timestamp\tuser\titem\tfeed\n", encoding="utf-8")


# =========================
# Bot core
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================
# Helpers
# =========================
def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_posted(user_id: int, item_id: str, feed: str) -> None:
    line = f"{utc_ts()}\t{user_id}\t{item_id}\t{feed}\n"
    with POSTED_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def feedback_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="👍", callback_data=f"like:{item_id}"),
            InlineKeyboardButton(text="👎", callback_data=f"skip:{item_id}"),
            InlineKeyboardButton(text="🚫", callback_data=f"ban:{item_id}"),
        ]]
    )


def save_admin_chat(chat_id: int) -> None:
    ADMIN_CHAT_FILE.write_text(str(chat_id), encoding="utf-8")


def load_admin_chat() -> int | None:
    if not ADMIN_CHAT_FILE.exists():
        return None
    try:
        return int(ADMIN_CHAT_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


async def send_video_with_feedback(chat_id: int, abs_path: str, item_id: str, caption: str | None = None) -> None:
    try:
        media = FSInputFile(abs_path)
        await bot.send_video(
            chat_id=chat_id,
            video=media,
            caption=caption or None,
            reply_markup=feedback_keyboard(item_id),
        )
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Не смог отправить видео: {item_id}\n{e}")


# =========================
# Basic commands
# =========================
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("✅ Бот запущен.")


@dp.message(Command("set_admin"))
async def set_admin_handler(message: Message):
    save_admin_chat(message.chat.id)
    await message.answer(f"✅ Admin chat сохранён: {message.chat.id}\nУтренний B будет слать в 06:00 МСК.")


@dp.message(Command("test"))
async def test_handler(message: Message):
    item_id = "test_item"
    await message.answer("Тестовый контент", reply_markup=feedback_keyboard(item_id))


# =========================
# Ingest commands
# =========================
@dp.message(Command("ingest12"))
async def cmd_ingest12(message: Message):
    await message.answer("⏳ Запускаю ingest за 12 часов...")
    await ingest_hours(12)
    await message.answer("✅ Ingest за 12 часов завершён.")


@dp.message(Command("ingest24"))
async def cmd_ingest24(message: Message):
    await message.answer("⏳ Запускаю ingest за 24 часа...")
    await ingest_hours(24)
    await message.answer("✅ Ingest за 24 часа завершён.")


# =========================
# Feedback handler
# =========================
@dp.callback_query()
async def feedback_handler(callback: types.CallbackQuery):
    if not callback.data or ":" not in callback.data:
        await callback.answer("bad callback")
        return

    action, item_id = callback.data.split(":", 1)
    user = callback.from_user.id

    line = f"{utc_ts()}\t{user}\t{item_id}\t{action}\n"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    await callback.answer(f"Сохранено: {action}")


# =========================
# Feed A VIDEO
# =========================
@dp.message(Command("feed_a_video"))
async def feed_a_video_handler(message: Message):
    try:
        from ranker import rank_top_n, CAT_A_VIDEO
    except Exception as e:
        await message.answer(f"❌ A ranker import error: {e}")
        return

    await message.answer("📤 Отправляю A VIDEO…")

    items = rank_top_n(
        user_id=message.from_user.id,
        category=CAT_A_VIDEO,
        n=20,
        feed="feed_a_video",
    )

    if not items:
        await message.answer("Пусто: A VIDEO не найдено.")
        return

    sent = 0
    for it in items:
        await send_video_with_feedback(message.chat.id, it.abs_path, it.item_id)
        log_posted(message.from_user.id, it.item_id, "feed_a_video")
        sent += 1

    await message.answer(f"✅ A VIDEO отправлено: {sent}")


# =========================
# Feed B VIDEO (NSFW API scoring)
# =========================
async def _send_b(chat_id: int, user_id: int, n: int, feed: str) -> int:
    # 1) score fresh B videos (writes b_nsfw_score into meta)
    try:
        from nsfw_runner import score_missing_b
        scored = await asyncio.to_thread(score_missing_b, 72, 30)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ B scoring error: {e}")
        return 0

    # 2) rank by b_nsfw_score
    try:
        from b_video_ranker import rank_b_videos
        items = rank_b_videos(user_id=user_id, n=n, feed=feed)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ B ranker error: {e}")
        return 0

    if not items:
        await bot.send_message(chat_id, f"Пусто: B VIDEO не найдено. (scored_now={scored})")
        return 0

    sent = 0
    for it in items:
        await send_video_with_feedback(chat_id, it.abs_path, it.item_id, caption=f"B score={it.score:.2f}")
        log_posted(user_id, it.item_id, feed)
        sent += 1

    await bot.send_message(chat_id, f"✅ B VIDEO отправлено: {sent} (scored_now={scored})")
    return sent


@dp.message(Command("feed_b_video"))
async def feed_b_video_handler(message: Message):
    await message.answer("📤 B VIDEO: скорю через NSFW API и отправляю… (до 3)")
    await _send_b(chat_id=message.chat.id, user_id=message.from_user.id, n=3, feed="feed_b_video")


# =========================
# Daily B scheduler 06:00 MSK (1 video)
# =========================
async def _sleep_until_next_0600_msk():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    target = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())


async def daily_b_loop():
    while True:
        try:
            await _sleep_until_next_0600_msk()
            chat_id = load_admin_chat()
            if not chat_id:
                continue
            # send exactly 1
            await _send_b(chat_id=chat_id, user_id=chat_id, n=1, feed="daily_b_video")
        except Exception:
            await asyncio.sleep(30)


# =========================
# Main
# =========================
async def main():
    asyncio.create_task(daily_b_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
