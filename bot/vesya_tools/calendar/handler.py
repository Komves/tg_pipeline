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


async def _may_manage_birthdays(message, group_id: int) -> bool:
    """Only Telegram group admins or explicitly configured administrators may edit."""
    import os
    user_id = int(message.from_user.id) if message.from_user else 0
    allowed = {int(x.strip()) for x in os.getenv("V_BIRTHDAY_ADMIN_IDS", "").split(",")
               if x.strip().isdigit()}
    if user_id and user_id in allowed:
        return True
    try:
        member = await message.bot.get_chat_member(int(group_id), user_id)
        status = str(getattr(member, "status", "")).lower()
        return status in ("creator", "administrator", "owner", "chatmemberstatus.creator",
                          "chatmemberstatus.administrator")
    except Exception as exc:
        print(f"[birthday] admin check failed: {type(exc).__name__}: {exc}", flush=True)
        return False


async def _resolve_birthday_name(message, storage, row: dict) -> str:
    name = str(row["person_name"])
    user_id = row.get("telegram_user_id")
    if not name.lower().startswith("id=") or not user_id:
        return name
    try:
        member = await message.bot.get_chat_member(int(row["group_id"]), int(user_id))
        user = member.user
        found_name = " ".join(p for p in (user.first_name, user.last_name) if p).strip()
        if found_name:
            storage.update_birthday_identity(int(row["id"]), found_name, user.username)
            return found_name
    except Exception as exc:
        print(f"[birthday] cannot resolve id={user_id}: {type(exc).__name__}: {exc}", flush=True)
    return name


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
            name = await _resolve_birthday_name(message, storage, row)
            birthday = str(row["birthday"])
            y, m, d = birthday.split("-")
            formatted_date = f"{int(d):02d}.{int(m):02d}"
            if y != "1900":
                formatted_date += f".{y}"
            full_name = str(row.get("full_name") or "").strip() or name
            nickname = str(row.get("nickname") or "").strip()
            label = f"{full_name} (зову {nickname})" if nickname and nickname.casefold() != full_name.casefold() else full_name
            lines.append(f"{label} — {formatted_date}")

        await message.answer("\n".join(lines))
        return True

    rename = re.match(
        r"^переименуй\s+др\s+группы\s+(.+?)\s+id=(\d+)\s+в\s+(.+)$",
        src, flags=re.I,
    )
    if rename:
        group_name, raw_id, new_name = rename.groups()
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу.")
        elif not await _may_manage_birthdays(message, int(group["group_id"])):
            await message.answer("Изменять список ДР может администратор группы.")
        elif storage.rename_birthday_by_telegram_id(
            int(group["group_id"]), int(raw_id), new_name, now_iso
        ):
            await message.answer(f"Запомнила имя: {new_name.strip()}. Дату сохранила.")
        else:
            await message.answer("Такого ID в списке ДР группы не нашла.")
        return True

    # Example (private chat):
    # Веся, запомни именинника группы <группа> id=123: Полное Имя — Прозвище
    person = re.match(
        r"^запомни\s+именинника\s+группы\s+(.+?)\s+id=(\d+)\s*:\s*(.+?)\s+[—-]\s+(.+)$",
        src, flags=re.I,
    )
    if person:
        group_name, raw_id, full_name, nickname = person.groups()
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу.")
            return True
        if not await _may_manage_birthdays(message, int(group["group_id"])):
            await message.answer("Изменять список ДР может администратор группы.")
            return True
        full_name, nickname = full_name.strip(), nickname.strip()
        if storage.set_birthday_names_by_telegram_id(
            int(group["group_id"]), int(raw_id), full_name, nickname, now_iso,
        ):
            await message.answer(
                f"Запомнила: {full_name}. В группе зову {nickname}. Дату рождения сохранила."
            )
        else:
            await message.answer("Не нашла этого ID в списке ДР или имя слишком длинное.")
        return True

    if low.startswith("добавь др для группы"):
        header, _, body = src.partition("\n")
        group_name = parse_group_name_after(header, "добавь др для группы")
        group = storage.find_group(user_id, group_name)
        if not group:
            await message.answer("Такую группу я пока не вижу. Сначала напиши там что-нибудь, чтобы я ее запомнила.")
            return True

        if not await _may_manage_birthdays(message, int(group["group_id"])):
            await message.answer("Добавлять ДР может администратор группы.")
            return True

        items = parse_birthday_lines(body, now.year)
        if not items:
            await message.answer("Не вижу дат. Формат: Серега — 5 марта")
            return True

        linked = 0

        for person_name, birthday in items:
            manual_id = re.fullmatch(r"(.+?)\s*\(id=(\d+)\)", person_name, flags=re.I)
            if manual_id:
                person_name = manual_id.group(1).strip()
            raw_id = manual_id.group(2) if manual_id else None
            if not raw_id and re.fullmatch(r"id=\d+", person_name, flags=re.I):
                raw_id = person_name.split("=", 1)[1]

            # Name (friendly address) (id=123) — DD.MM.YYYY
            full_name = person_name
            nickname = None
            familiar = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", person_name)
            if familiar:
                full_name = familiar.group(1).strip()
                nickname = familiar.group(2).strip()
                if not full_name or not nickname:
                    continue

            member = storage.find_group_member(int(group["group_id"]), full_name)
            telegram_user_id = int(raw_id) if raw_id else (
                int(member["telegram_user_id"]) if member else None
            )
            username = str(member["username"]) if member and member.get("username") else None

            if telegram_user_id:
                linked += 1

            storage.upsert_birthday(
                group_id=int(group["group_id"]),
                owner_user_id=user_id,
                group_title=str(group["group_title"]),
                person_name=full_name,
                birthday=birthday,
                now_iso=now_iso,
                telegram_user_id=telegram_user_id,
                username=username,
                full_name=full_name if nickname else None,
                nickname=nickname,
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

        if not await _may_manage_birthdays(message, int(found[0]["group_id"])):
            await message.answer("Исправлять ДР может администратор группы.")
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

        if not await _may_manage_birthdays(message, int(found[0]["group_id"])):
            await message.answer("Удалять ДР может администратор группы.")
            return True
        storage.delete_birthday(int(found[0]["id"]))
        await message.answer("Удалила.")
        return True

    return False