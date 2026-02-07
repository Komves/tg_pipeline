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
from ranker import rank_top_n, CAT_A_VIDEO

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_CHAT_FILE = DATA_DIR / "admin_chat_id.txt"
LAST_DAILY_FILE = DATA_DIR / "last_daily_run_utc.txt"

# 06:00 MSK = 03:00 UTC (MSK = UTC+3 без переходов)
DAILY_UTC_HOUR = 3
DAILY_UTC_MIN = 0

DAILY_INGEST_HOURS = 24
DAILY_SEND_N = 20
DAILY_FEED_NAME = "feed_a_video_daily"

# feedback storage bootstrap
if not os.path.exists(FEEDBACK_FILE):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        f.write("timestamp\tuser\titem\taction\n")

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


def last_daily_run_key() -> str | None:
    if not LAST_DAILY_FILE.exists():
        return None
    return LAST_DAILY_FILE.read_text(encoding="utf-8").strip() or None


def set_last_daily_run_key(key: str) -> None:
    LAST_DAILY_FILE.write_text(key, encoding="utf-8")


def next_run_utc(now: datetime) -> datetime:
    # target today at 03:00 UTC, else tomorrow
    target = now.replace(hour=DAILY_UTC_HOUR, minute=DAILY_UTC_MIN, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


async def daily_job_loop() -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            run_at = next_run_utc(now)
            sleep_s = max(1.0, (run_at - now).total_seconds())
            await asyncio.sleep(sleep_s)

            # защита от двойного запуска
            run_key = run_at.strftime("%Y-%m-%d")  # UTC date for 03:00 UTC
            if last_daily_run_key() == run_key:
                continue

            admin_chat_id = load_admin_chat_id()
            if not admin_chat_id:
                # некому слать — просто не запускаем рассылку, но и не помечаем run
                continue

            await bot.send_message(admin_chat_id, "⏳ Daily: запускаю ingest24 (06:00 МСК)…")
            await ingest_hours(DAILY_INGEST_HOURS)
            await bot.send_message(admin_chat_id, "✅ Daily: ingest завершён. Формирую выдачу…")

            items = rank_top_n(
                user_id=admin_chat_id,          # используем chat_id как user_id для posted/вкуса
                category=CAT_A_VIDEO,
                n=DAILY_SEND_N,
                feed=DAILY_FEED_NAME,
            )

            if not items:
                await bot.send_message(admin_chat_id, "Daily: новых A-видео не нашёл.")
                set_last_daily_run_key(run_key)
                continue

            sent = 0
            for it in items:
                try:
                    await bot.send_video(
                        chat_id=admin_chat_id,
                        video=FSInputFile(it.abs_path),
                        caption=f"{it.tg_date_iso}\n{it.src}",
                        reply_markup=feedback_keyboard(it.item_id),
                    )
                    sent += 1
                except Exception as e:
                    # не падаем из-за одного файла
                    await bot.send_message(admin_chat_id, f"send error: {e}")

            await bot.send_message(admin_chat_id, f"✅ Daily: отправил {sent}/{len(items)} видео.")
            set_last_daily_run_key(run_key)

        except Exception as e:
            # если что-то сломалось — ждём минуту и снова
            try:
                admin_chat_id = load_admin_chat_id()
                if admin_chat_id:
                    await bot.send_message(admin_chat_id, f"⚠️ Daily loop error: {e}")
            except Exception:
                pass
            await asyncio.sleep(60)


# --- commands ---
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Бот запущен. Для ежедневной рассылки сделай /set_admin в личке.")


@dp.message(Command("set_admin"))
async def set_admin_handler(message: Message):
    # сохраняем chat_id, куда слать daily
    save_admin_chat_id(message.chat.id)
    await message.answer(f"✅ Admin chat сохранён: {message.chat.id}\nDaily будет слать в 06:00 МСК.")


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


@dp.message(Command("feed_a_video"))
async def feed_a_video_handler(message: Message):
    items = rank_top_n(
        user_id=message.from_user.id,
        category=CAT_A_VIDEO,
        n=20,
        feed="feed_a_video",
    )
    if not items:
        await message.answer("Нет новых видео.")
        return

    for it in items:
        await message.answer_video(
            video=FSInputFile(it.abs_path),
            caption=f"{it.tg_date_iso}\n{it.src}",
            reply_markup=feedback_keyboard(it.item_id),
        )


@dp.callback_query()
async def feedback_handler(callback: types.CallbackQuery):
    action, item_id = callback.data.split(":", 1)
    user = callback.from_user.id
    timestamp = datetime.utcnow().isoformat()

    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp}\t{user}\t{item_id}\t{action}\n")

    await callback.answer(f"Сохранено: {action}")


async def main():
    # стартуем daily loop параллельно polling
    asyncio.create_task(daily_job_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
