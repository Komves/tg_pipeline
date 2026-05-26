from __future__ import annotations

import asyncio
import os
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from .birthday import build_birthday_message


async def calendar_loop(bot, storage) -> None:
    tz_name = os.getenv("V_CALENDAR_TZ", "Europe/Moscow")
    check_sec = int(os.getenv("V_CALENDAR_CHECK_SEC", "60"))
    birthday_hour = int(os.getenv("V_BIRTHDAY_HOUR", "9"))
    birthday_jitter_min = int(os.getenv("V_BIRTHDAY_JITTER_MIN", "40"))

    tz = ZoneInfo(tz_name)

    while True:
        try:
            now = datetime.now(tz)
            now_iso = now.isoformat()

            for item in storage.due_reminders(now_iso):
                try:
                    await bot.send_message(
                        int(item["chat_id"]),
                        f"Напоминаю: {item['text']}",
                    )
                    storage.mark_reminder_sent(int(item["id"]), now_iso)
                except Exception as e:
                    print(f"[calendar] reminder send failed: {type(e).__name__}: {e}", flush=True)

            month_day = now.strftime("%m-%d")

            for item in storage.todays_birthdays(month_day, now.year):
                try:
                    seed = (
                        f"{now.year}:"
                        f"{item['group_id']}:"
                        f"{item['person_name']}"
                    )

                    digest = hashlib.sha256(seed.encode("utf-8")).digest()

                    jitter = int.from_bytes(digest[:2], "big") % max(1, birthday_jitter_min + 1)

                    target_hour = birthday_hour
                    target_minute = jitter

                    if (
                        now.hour > target_hour
                        or (
                            now.hour == target_hour
                            and now.minute >= target_minute
                        )
                    ):
                        await bot.send_message(
                            int(item["group_id"]),
                            build_birthday_message(
                                str(item["person_name"]),
                                int(item["telegram_user_id"]) if item.get("telegram_user_id") else None,
                                str(item["username"]) if item.get("username") else None,
                            ),
                            parse_mode="HTML",
                        )

                        storage.mark_birthday_congratulated(
                            int(item["id"]),
                            now.year,
                            now_iso,
                        )

                except Exception as e:
                    print(
                        f"[calendar] birthday send failed: {type(e).__name__}: {e}",
                        flush=True,
                    )

        except Exception as e:
            print(f"[calendar] loop failed: {type(e).__name__}: {e}", flush=True)

        await asyncio.sleep(check_sec)