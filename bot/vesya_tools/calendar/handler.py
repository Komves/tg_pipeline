from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .group_registry import register_seen_group
from .parser import (
    parse_birthday_date,
    parse_birthday_lines,
    parse_group_name_after,
    parse_reminder,
    strip_vesya,
)


async def handle_calendar_message(message, storage) -> bool:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    now_iso = now.isoformat()

    await register_seen_group(storage, message, now_iso)

    text = (message.text or "").strip()
    src = strip_vesya(text)
    low = src.lower()
    user_id = int(message.from_user.id) if message.from_user else 0

    if message.chat.type == "private":
        reminder = parse_reminder(text, now)
        if reminder:
            storage.add_reminder(
                chat_id=int(message.chat.id),
                user_id=user_id,
                text=reminder.text,
                remind_at=reminder.remind_at.isoformat(),
                created_at=now_iso,
            )
            await message.answer("Ок, напомню.")
            return True

    if message.chat.type != "private":
        return False

    if low in ("покажи мои группы", "покажи группы", "мои группы"):
        groups = storage.list_groups(user_id)
        if not groups:
            await message.answer("Я пока не видела тебя ни в одной группе.")
            return True

        lines = [f"{i}. {g['group_title']}" for i, g in enumerate(groups, 1)]
        await message.answer("\n".join(lines))
        return True

    if low.startswith("покажи участников группы"):
        group_name = parse_group_name_after(src, "покажи участников группы")
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу.")
            return True

        rows = storage.list_group_members(int(group["group_id"]))
        if not rows:
            await message.answer("По этой группе я пока не вижу участников.")
            return True

        lines = []
        for i, row in enumerate(rows, 1):
            username = str(row.get("username") or "").strip()
            username_part = f"@{username}" if username else "без username"
            full_name = str(row.get("full_name") or "").strip() or "без имени"
            user_id_part = str(row.get("telegram_user_id") or "")
            lines.append(f"{i}. {full_name} | {username_part} | id={user_id_part}")

        await message.answer("\n".join(lines[:80]))
        return True

    if low.startswith("покажи др группы"):
        group_name = parse_group_name_after(src, "покажи др группы")
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу.")
            return True

        rows = storage.list_birthdays(user_id, int(group["group_id"]))
        if not rows:
            await message.answer("По этой группе ДР пока не записаны.")
            return True

        lines = []
        for row in rows:
            birthday = str(row["birthday"])
            y, m, d = birthday.split("-")
            if y == "1900":
                lines.append(f"{row['person_name']} — {int(d):02d}.{int(m):02d}")
            else:
                lines.append(f"{row['person_name']} — {int(d):02d}.{int(m):02d}.{y}")

        await message.answer("\n".join(lines))
        return True

    if low.startswith("добавь др для группы"):
        header, _, body = src.partition("\n")
        group_name = parse_group_name_after(header, "добавь др для группы")
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу. Сначала напиши там что-нибудь, чтобы я ее запомнила.")
            return True

        items = parse_birthday_lines(body, now.year)
        if not items:
            await message.answer("Не вижу дат. Формат: Серега — 5 марта")
            return True

        linked = 0

        for person_name, birthday in items:
            member = storage.find_group_member(int(group["group_id"]), person_name)
            telegram_user_id = int(member["telegram_user_id"]) if member else None
            username = str(member["username"]) if member and member.get("username") else None

            if telegram_user_id:
                linked += 1

            storage.upsert_birthday(
                group_id=int(group["group_id"]),
                owner_user_id=user_id,
                group_title=str(group["group_title"]),
                person_name=person_name,
                birthday=birthday,
                now_iso=now_iso,
                telegram_user_id=telegram_user_id,
                username=username,
            )

        await message.answer(f"Записала ДР: {len(items)}. Привязала к участникам: {linked}.")
        return True

    m = re.match(r"^исправь\s+др\s+(.+?)\s+на\s+(.+)$", src, flags=re.I)
    if m:
        person_name = m.group(1).strip()
        birthday = parse_birthday_date(m.group(2).strip(), now.year)
        if not birthday:
            await message.answer("Не поняла дату.")
            return True

        found = storage.find_birthday_by_person(user_id, person_name)
        if not found:
            await message.answer("Такого ДР не нашла.")
            return True
        if len(found) > 1:
            await message.answer("Нашла в нескольких группах. Уточни группу.")
            return True

        storage.update_birthday(int(found[0]["id"]), birthday, now_iso)
        await message.answer("Исправила.")
        return True

    m = re.match(
        r"^удали\s+др\s+(.+?)(?:\s+из\s+группы\s+(.+))?$",
        src,
        flags=re.I,
    )

    if m:
        person_name = m.group(1).strip()
        group_name = (m.group(2) or "").strip()

        if group_name:
            group = storage.find_group(user_id, group_name)

            if not group:
                await message.answer("Такую группу я пока не вижу.")
                return True

            found = storage.find_birthday_by_person_in_group(
                user_id,
                int(group["group_id"]),
                person_name,
            )
        else:
            found = storage.find_birthday_by_person(user_id, person_name)
        if not found:
            await message.answer("Такого ДР не нашла.")
            return True
        if len(found) > 1:
            await message.answer("Нашла в нескольких группах. Уточни группу.")
            return True

        storage.delete_birthday(int(found[0]["id"]))
        await message.answer("Удалила.")
        return True

    return False