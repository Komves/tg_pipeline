from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

RU_HOURS = {
    "ноль": 0,
    "один": 1,
    "час": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "двадцать один": 21,
    "двадцать два": 22,
    "двадцать три": 23,
}


@dataclass(frozen=True)
class ReminderParse:
    remind_at: datetime
    text: str


def strip_vesya(text: str) -> str:
    return re.sub(
        r"^\s*(веся|вися|веська|веслава|vesya|сергеевна)[\s,.:;!-]+",
        "",
        text or "",
        flags=re.I,
    ).strip()


def _parse_time(value: str | None) -> tuple[int, int]:
    s = (value or "").strip().lower()
    if not s:
        return 9, 0

    m = re.search(r"(\d{1,2})(?:[:.\-](\d{2}))?", s)
    if m:
        hour = max(0, min(23, int(m.group(1))))
        minute = max(0, min(59, int(m.group(2) or 0)))
    else:
        hour = None
        minute = 0

        normalized = re.sub(r"\s+", " ", s).strip()
        for word, value_hour in sorted(RU_HOURS.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf"\b{re.escape(word)}\b", normalized, flags=re.I):
                hour = int(value_hour)
                break

        if hour is None:
            return 9, 0

    if re.search(r"\bвечера\b", s) and 1 <= hour <= 11:
        hour += 12

    if re.search(r"\bдня\b", s) and 1 <= hour <= 11:
        hour += 12

    if re.search(r"\bночи\b", s) and hour == 12:
        hour = 0

    return hour, minute


def parse_reminder(text: str, now: datetime) -> ReminderParse | None:
    src = strip_vesya(text)
    low = src.lower()

    if not low.startswith("напомни"):
        return None

    body = re.sub(r"^напомни\s*", "", src, flags=re.I).strip()

    m = re.match(
        r"^через\s+(минуту|час|день|год)\s*(.*)$",
        body,
        flags=re.I,
    )
    if m:
        unit = m.group(1).lower()
        reminder_text = m.group(2).strip(" ,.-") or "напоминание"

        if unit == "минуту":
            dt = now + timedelta(minutes=1)
        elif unit == "час":
            dt = now + timedelta(hours=1)
        elif unit == "день":
            dt = now + timedelta(days=1)
        else:
            dt = now + timedelta(days=365)

        return ReminderParse(remind_at=dt, text=reminder_text)

    m = re.match(
        r"^через\s+(\d+|один|одну|год)\s+(минуту|минут|минуты|час|часа|часов|день|дня|дней|год|года|лет)\s*(.*)$",
        body,
        flags=re.I,
    )

    if m:
        n_raw = m.group(1).lower()
        unit = m.group(2).lower()
        reminder_text = m.group(3).strip(" ,.-") or "напоминание"

        if n_raw in ("один", "одну"):
            n = 1
        elif n_raw == "год":
            n = 1
            unit = "год"
        else:
            n = int(n_raw)

        if unit.startswith("минут"):
            dt = now + timedelta(minutes=n)
        elif unit.startswith("час"):
            dt = now + timedelta(hours=n)
        elif unit.startswith("д"):
            dt = now + timedelta(days=n)
        else:
            dt = now + timedelta(days=365 * n)

        return ReminderParse(remind_at=dt, text=reminder_text)

    m = re.match(
        r"^(сегодня|завтра)(?:\s+в\s*с?\s*((?:\d{1,2}(?:(?::|\.|-)\d{2})?|[а-яё]+(?:\s+[а-яё]+)?)(?:\s*(?:утра|дня|вечера|ночи))?))?\s*(.*)$",
        body,
        flags=re.I,
    )

    if m:
        day_word = m.group(1).lower()
        hour, minute = _parse_time(m.group(2))
        reminder_text = m.group(3).strip(" ,.-") or "напоминание"

        days = 1 if day_word == "завтра" else 0
        dt = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)

        if day_word == "сегодня" and dt <= now:
            return None

        return ReminderParse(remind_at=dt, text=reminder_text)

    m = re.match(
        r"^(\d{1,2})\s+([а-яё]+)(?:\s+в\s+((?:\d{1,2}(?:(?::|\.|-)\d{2})?|[а-яё]+(?:\s+[а-яё]+)?)(?:\s*(?:утра|дня|вечера|ночи))?))?\s*(.*)$",
        body,
        flags=re.I,
    )
    if m:
        day = int(m.group(1))
        month = RU_MONTHS.get(m.group(2).lower())
        if not month:
            return None

        hour, minute = _parse_time(m.group(3))
        year = now.year
        dt = datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
        if dt <= now:
            dt = datetime(year + 1, month, day, hour, minute, tzinfo=now.tzinfo)

        reminder_text = m.group(4).strip(" ,.-") or "напоминание"
        return ReminderParse(remind_at=dt, text=reminder_text)

    return None


def parse_group_name_after(text: str, prefix: str) -> str:
    src = strip_vesya(text)
    return re.sub(rf"^{re.escape(prefix)}\s*", "", src, flags=re.I).strip(" .")


def parse_birthday_date(value: str, now_year: int) -> str | None:
    s = value.strip().lower()

    m = re.match(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?$", s, flags=re.I)
    if not m:
        return None

    day = int(m.group(1))
    month = RU_MONTHS.get(m.group(2).lower())
    if not month:
        return None

    year = int(m.group(3) or 1900)

    try:
        datetime(year, month, day)
    except ValueError:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_birthday_lines(text: str, now_year: int) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []

    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue

        normalized = line.replace(" - ", " — ").replace("-", " — ")

        if "—" not in normalized:
            continue

        name, date_raw = normalized.split("—", 1)
        name = name.strip()
        birthday = parse_birthday_date(date_raw.strip(), now_year)

        if name and birthday:
            result.append((name, birthday))

    return result