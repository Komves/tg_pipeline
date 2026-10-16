from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .birthday import generate_birthday_message


async def _resolve_person(bot, storage, item: dict) -> dict:
    """Fill old id=NNN names if Telegram allows access; keep IDs if not."""
    row = dict(item)
    name = str(row["person_name"])
    user_id = row.get("telegram_user_id")
    if not user_id and re.fullmatch(r"id=\d+", name, flags=re.I):
        user_id = int(name.split("=", 1)[1])
        row["telegram_user_id"] = user_id

    if not user_id or not name.lower().startswith("id="):
        return row

    try:
        member = await bot.get_chat_member(int(row["group_id"]), int(user_id))
        user = member.user
        full_name = " ".join(p for p in (user.first_name, user.last_name) if p).strip()
        if full_name:
            row["person_name"] = full_name
            row["username"] = user.username
            storage.update_birthday_identity(int(row["id"]), full_name, user.username)
    except Exception as exc:
        print(f"[birthday] cannot resolve user {user_id}: {type(exc).__name__}: {exc}", flush=True)
    return row


async def _send_birthday(bot, storage, item: dict) -> None:
    """One Telegram message: video with greeting in caption, or text fallback."""
    item = await _resolve_person(bot, storage, item)
    friendly_name = (str(item.get("nickname") or "").strip()
                     or str(item.get("full_name") or "").strip()
                     or str(item["person_name"]))
    caption = await generate_birthday_message(
        friendly_name,
        str(item["group_title"]),
        int(item["telegram_user_id"]) if item.get("telegram_user_id") else None,
        str(item["username"]) if item.get("username") else None,
    )

    # The ordinary 'дай красоты' command uses this same pool.
    from beauty_pool import pick_unseen_beauty_clip, mark_beauty_clip_sent
    from aiogram.types import FSInputFile

    group_id = int(item["group_id"])
    clip = pick_unseen_beauty_clip(group_id)
    if not clip:
        await bot.send_message(group_id, caption, parse_mode="HTML")
        return

    clip_id = str(clip.get("id") or "")
    video_path = Path(str(clip.get("path") or clip.get("abs_path") or ""))
    if not clip_id or not video_path.is_file():
        # A corrupt pool entry should not prevent a birthday greeting.
        await bot.send_message(group_id, caption, parse_mode="HTML")
        return

    last_error = None
    for attempt in range(3):
        try:
            await bot.send_video(
                group_id,
                video=FSInputFile(str(video_path)),
                caption=caption,
                parse_mode="HTML",
                request_timeout=int(os.getenv("V_VIDEO_SEND_TIMEOUT", "45")),
            )
            mark_beauty_clip_sent(group_id, clip_id)
            return
        except Exception as exc:
            last_error = exc
            print(
                f"[birthday] video send {attempt + 1}/3 failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            if attempt < 2:
                await asyncio.sleep(2 + 2 * attempt)
    # Do not replace a failed video with a second text message: Telegram may
    # have accepted the first video before the API connection failed.
    raise last_error or RuntimeError("birthday video delivery failed")


async def calendar_loop(bot, storage) -> None:
    tz_name = (
        os.getenv("V_BIRTHDAY_TZ")
        or os.getenv("V_RUNTIME_TZ")
        or os.getenv("V_CALENDAR_TZ")
        or "Europe/Moscow"
    )
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
                except Exception as exc:
                    print(f"[calendar] reminder failed: {type(exc).__name__}: {exc}", flush=True)

            month_day = now.strftime("%m-%d")

            for item in storage.todays_birthdays(month_day, now.year):
                try:
                    seed = f"{now.year}:{item['group_id']}:{item['telegram_user_id'] or item['person_name']}"
                    digest = hashlib.sha256(seed.encode("utf-8")).digest()
                    jitter = int.from_bytes(digest[:2], "big") % max(1, birthday_jitter_min + 1)
                    if (now.hour, now.minute) < (birthday_hour, jitter):
                        continue

                    await _send_birthday(bot, storage, item)
                    storage.mark_birthday_congratulated(int(item["id"]), now.year, now_iso)
                except Exception as exc:
                    print(f"[calendar] birthday failed: {type(exc).__name__}: {exc}", flush=True)

        except Exception as exc:
            print(f"[calendar] loop failed: {type(exc).__name__}: {exc}", flush=True)

        await asyncio.sleep(check_sec)
