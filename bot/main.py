import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from config import BOT_TOKEN
from config import FEEDBACK_FILE
import os

os.makedirs("/data", exist_ok=True)

if not os.path.exists(FEEDBACK_FILE):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write("timestamp\tuser\titem\taction\n")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Бот запущен и работает.")

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

def feedback_keyboard(item_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👍", callback_data=f"like:{item_id}"),
            InlineKeyboardButton(text="👎", callback_data=f"skip:{item_id}"),
            InlineKeyboardButton(text="🚫", callback_data=f"ban:{item_id}")
        ]
    ])

@dp.message(Command("test"))
async def test_handler(message: types.Message):
    item_id = "test_item"
    await message.answer(
        "Тестовый контент",
        reply_markup=feedback_keyboard(item_id)
    )

@dp.callback_query()
async def feedback_handler(callback: types.CallbackQuery):
    action, item_id = callback.data.split(":")
    user = callback.from_user.id
    timestamp = datetime.utcnow().isoformat()

    line = f"{timestamp}\t{user}\t{item_id}\t{action}\n"

    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    await callback.answer(f"Сохранено: {action}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
