from ranker import rank_top_n, CAT_A_VIDEO
import asyncio
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import BOT_TOKEN, FEEDBACK_FILE
from ingest_runner import ingest_hours


# --- filesystem bootstrap ---
os.makedirs("/data", exist_ok=True)

if not os.path.exists(FEEDBACK_FILE):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write("timestamp\tuser\titem\taction\n")


# --- bot core ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- ingest commands ---
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


# --- start / health ---
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Бот запущен и работает.")


# --- feedback UI ---
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


@dp.message(Command("test"))
async def test_handler(message: Message):
    item_id = "test_item"
    await message.answer("Тестовый контент", reply_markup=feedback_keyboard(item_id))


@dp.callback_query()
async def feedback_handler(callback: types.CallbackQuery):
    # expected: "like:<id>", "skip:<id>", "ban:<id>"
    action, item_id = callback.data.split(":", 1)

    user = callback.from_user.id
    timestamp = datetime.utcnow().isoformat()

    line = f"{timestamp}\t{user}\t{item_id}\t{action}\n"
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    await callback.answer(f"Сохранено: {action}")


# --- feed ---
@dp.message(Command("feed_a_video"))
async def feed_a_video_handler(message: Message):
    items = rank_top_n(
        user_id=message.from_user.id,
        category=CAT_A_VIDEO,
        n=5
    )

    if not items:
        await message.answer("Нет видео")
        return

    for item in items:
        await message.answer_video(open(item.abs_path, "rb"), reply_markup=feedback_keyboard(item.item_id))


# --- main ---
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
