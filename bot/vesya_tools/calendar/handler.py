from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

from .group_registry import register_seen_group

from .parser import (
    parse_birthday_date,
    parse_birthday_lines,
    parse_group_name_after,
    parse_reminder,
    strip_vesya,
)


TZ_ALIASES = {
    # Россия
    "екат": "Asia/Yekaterinburg",
    "екб": "Asia/Yekaterinburg",
    "екатеринбург": "Asia/Yekaterinburg",
    "екатеринбурге": "Asia/Yekaterinburg",
    "свердловск": "Asia/Yekaterinburg",
    "мск": "Europe/Moscow",
    "москва": "Europe/Moscow",
    "москве": "Europe/Moscow",
    "московское": "Europe/Moscow",
    "омск": "Asia/Omsk",
    "омске": "Asia/Omsk",
    "новосибирск": "Asia/Novosibirsk",
    "новосибирске": "Asia/Novosibirsk",
    "красноярск": "Asia/Krasnoyarsk",
    "иркутск": "Asia/Irkutsk",
    "владивосток": "Asia/Vladivostok",
    "калининград": "Europe/Kaliningrad",

    # Европа
    "амстердам": "Europe/Amsterdam",
    "амстердаме": "Europe/Amsterdam",
    "берлин": "Europe/Berlin",
    "берлине": "Europe/Berlin",
    "париж": "Europe/Paris",
    "париже": "Europe/Paris",
    "лондон": "Europe/London",
    "лондоне": "Europe/London",
    "прага": "Europe/Prague",
    "праге": "Europe/Prague",

    # США / Канада
    "нью-йорк": "America/New_York",
    "нью йорк": "America/New_York",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "вашингтон": "America/New_York",
    "бостон": "America/New_York",
    "майами": "America/New_York",
    "чикаго": "America/Chicago",
    "chicago": "America/Chicago",
    "техас": "America/Chicago",
    "хьюстон": "America/Chicago",
    "даллас": "America/Chicago",
    "денвер": "America/Denver",
    "denver": "America/Denver",
    "финикс": "America/Phoenix",
    "phoenix": "America/Phoenix",
    "лос-анджелес": "America/Los_Angeles",
    "лос анджелес": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "сан-франциско": "America/Los_Angeles",
    "сан франциско": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "сиэтл": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "торонто": "America/Toronto",
    "toronto": "America/Toronto",
    "ванкувер": "America/Vancouver",
    "vancouver": "America/Vancouver",

    # Ближний восток / Кавказ / Азия
    "дубай": "Asia/Dubai",
    "дубае": "Asia/Dubai",
    "тбилиси": "Asia/Tbilisi",
    "ереван": "Asia/Yerevan",
    "ереване": "Asia/Yerevan",
    "ташкент": "Asia/Tashkent",
    "ташкенте": "Asia/Tashkent",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "бангкок": "Asia/Bangkok",
    "бангкоке": "Asia/Bangkok",
}


def _clean_timezone_phrase(value: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"[«»\"']", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .,!?:;")

    raw = re.sub(
        r"^(?:"
        r"запомни|сохрани|поставь|установи|задай|измени|поменяй"
        r")\s+",
        "",
        raw,
        flags=re.I,
    )

    raw = re.sub(
        r"^(?:"
        r"мой\s+часовой\s+пояс|"
        r"у\s+меня\s+часовой\s+пояс|"
        r"часовой\s+пояс|"
        r"моя\s+таймзона|"
        r"таймзона|"
        r"timezone|"
        r"time\s+zone|"
        r"время\s+у\s+меня|"
        r"я\s+нахожусь\s+в|"
        r"я\s+сейчас\s+в|"
        r"я\s+живу\s+в|"
        r"живу\s+в|"
        r"я\s+в|"
        r"в"
        r")\s+",
        "",
        raw,
        flags=re.I,
    )

    raw = re.sub(
        r"\s+(?:"
        r"мой\s+часовой\s+пояс|"
        r"часовой\s+пояс|"
        r"таймзона|"
        r"timezone|"
        r"time\s+zone"
        r")$",
        "",
        raw,
        flags=re.I,
    )

    return re.sub(r"\s+", " ", raw).strip(" .,!?:;")


def _resolve_timezone_name(value: str) -> str | None:
    raw = _clean_timezone_phrase(value)
    if not raw:
        return None

    key = raw.lower().replace("ё", "е")
    key = key.strip(" .,!?:;")

    if key in TZ_ALIASES:
        return TZ_ALIASES[key]

    # Точный IANA-формат: Europe/Amsterdam, America/New_York, Asia/Yekaterinburg.
    if re.fullmatch(r"[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?", raw):
        try:
            ZoneInfo(raw)
            return raw
        except Exception:
            return None

    # UTC/GMT-смещение НЕ превращаем в город.
    # UTC+5 может быть Екатеринбург, Ташкент, Мальдивы, Пакистан и т.д.
    if re.fullmatch(r"(?:utc|gmt)?\s*[+-]\s*\d{1,2}(?::?\d{2})?", key, flags=re.I):
        return None

    # Английское название города пробуем сопоставить с IANA-зоной по хвосту.
    # Например: New York -> America/New_York, Los Angeles -> America/Los_Angeles.
    city_token = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9\s_-]+", " ", raw)
    city_token = re.sub(r"\s+", "_", city_token.strip())
    city_token_lower = city_token.lower()

    matches = []
    for zone in available_timezones():
        tail = zone.rsplit("/", 1)[-1].lower()
        if tail == city_token_lower:
            matches.append(zone)

    if len(matches) == 1:
        return matches[0]

    return None


async def handle_calendar_message(message, storage) -> bool:
    import os

    text = (message.text or "").strip()
    src = strip_vesya(text)
    low = src.lower()
    user_id = int(message.from_user.id) if message.from_user else 0

    fallback_tz_name = (
        os.getenv("V_RUNTIME_TZ")
        or os.getenv("V_CALENDAR_TZ")
        or "Europe/Moscow"
    )
    user_tz_name = storage.get_user_timezone(user_id) if user_id else None
    tz_name = user_tz_name or fallback_tz_name

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = fallback_tz_name
        tz = ZoneInfo(tz_name)

    now = datetime.now(tz)
    now_iso = now.isoformat()

    print(
        f"[calendar] user_id={user_id} tz={tz_name} "
        f"user_tz={user_tz_name!r} fallback_tz={fallback_tz_name!r} "
        f"now={now_iso} text={text!r}",
        flush=True,
    )

    await register_seen_group(storage, message, now_iso)

    timezone_command = bool(re.search(
        r"\b("
        r"часов(?:ой|ого|ом)\s+пояс|"
        r"таймзон|"
        r"timezone|"
        r"time\s+zone|"
        r"время\s+у\s+меня"
        r")\b",
        src,
        flags=re.I,
    ))

    if timezone_command:
        requested_tz = _resolve_timezone_name(src)

        if not requested_tz:
            await message.answer(
                "Не смогла однозначно определить часовой пояс. "
                "Напиши город или IANA-зону, например: "
                "Asia/Yekaterinburg, Europe/Amsterdam, America/New_York."
            )
            print(
                f"[calendar_tz] failed user_id={user_id} raw={src!r}",
                flush=True,
            )
            return True

        storage.set_user_timezone(user_id, requested_tz, now_iso)
        local_now = datetime.now(ZoneInfo(requested_tz))

        await message.answer(
            f"Запомнила. Твой часовой пояс: {requested_tz}. "
            f"Сейчас у тебя {local_now.strftime('%H:%M')}."
        )
        print(
            f"[calendar_tz] saved user_id={user_id} timezone={requested_tz} "
            f"local_now={local_now.isoformat()} raw={src!r}",
            flush=True,
        )
        return True

    if low in ("мой часовой пояс", "покажи мой часовой пояс", "какой у меня часовой пояс"):
        if user_tz_name:
            await message.answer(f"Твой часовой пояс: {user_tz_name}.")
        else:
            await message.answer(
                "Твой часовой пояс пока не задан. Напиши, например: "
                "Веся, мой часовой пояс Екатеринбург"
            )
        return True

    reminder = parse_reminder(text, now)
    if reminder:
        remind_at_iso = reminder.remind_at.isoformat()

        print(
            f"[calendar] parsed reminder "
            f"now={now_iso} remind_at={remind_at_iso} "
            f"delta_sec={(reminder.remind_at - now).total_seconds():.0f} "
            f"reminder_text={reminder.text!r}",
            flush=True,
        )

        storage.add_reminder(
            chat_id=int(message.chat.id),
            user_id=user_id,
            text=reminder.text,
            remind_at=remind_at_iso,
            created_at=now_iso,
        )

        print(
            f"[calendar] stored reminder "
            f"chat_id={int(message.chat.id)} user_id={user_id} "
            f"remind_at={remind_at_iso}",
            flush=True,
        )

        await message.answer(
            f"Ок, напомню {reminder.remind_at.strftime('%d.%m в %H:%M')}: {reminder.text}"
        )
        return True

    print(
        f"[calendar] not handled text={text!r} src={src!r} low={low!r}",
        flush=True,
    )

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