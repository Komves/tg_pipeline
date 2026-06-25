from __future__ import annotations

import re
import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import openai
from docx import Document
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

SECRETARY_CACHE = {}

# =========================
# EMAIL UTILS
# =========================

def _handle_email_selection(message, text, cache):
    nums = [x.strip() for x in text.split(",")]

    selected = []

    for n in nums:
        if not n.isdigit():
            continue

        idx = int(n) - 1
        if 0 <= idx < len(cache["emails"]):
            selected.append(cache["emails"][idx])

    cache["selected"] = selected
    return selected


def _decode_mime(text):
    if not text:
        return ""

    parts = decode_header(text)
    out = ""

    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                out += part.decode(enc or "utf-8", errors="ignore")
            except Exception:
                out += part.decode("utf-8", errors="ignore")
        else:
            out += str(part)

    return out


def _fetch_mailru_emails(limit=30):
    user = os.getenv("MAILRU_EMAIL", "comves@list.ru")
    password = os.getenv("MAILRU_APP_PASSWORD")

    if not password:
        return []

    imap = imaplib.IMAP4_SSL("imap.mail.ru", 993)
    imap.login(user, password)

    results = []

    for folder in ["INBOX", "Sent"]:
        try:
            imap.select(folder)
            status, data = imap.search(None, "ALL")

            if status != "OK":
                continue

            ids = data[0].split()[-limit:]

            for num in ids:
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                subject = _decode_mime(msg.get("Subject"))
                from_ = _decode_mime(msg.get("From"))

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                body = part.get_payload(decode=True).decode(errors="ignore")
                                break
                            except Exception:
                                pass
                else:
                    try:
                        body = msg.get_payload(decode=True).decode(errors="ignore")
                    except Exception:
                        body = ""

                results.append({
                    "from": from_,
                    "subject": subject,
                    "body": body[:2000],
                })

        except Exception:
            continue

    imap.logout()
    return results


# =========================
# GROUPING
# =========================

def _build_cases(emails):
    cases = {}

    for e in emails:

        raw_from = e.get("from", "unknown")

        match = re.search(r'[\w\.-]+@[\w\.-]+', raw_from)
        sender = match.group(0).lower() if match else raw_from.lower().strip()

        subject = (e.get("subject") or "").lower().strip()
        subject = re.sub(r"^(re:|fw:|fwd:)\s*", "", subject)
        subject = re.sub(r"\s+", " ", subject)

        key = f"{sender}::{subject[:60]}"

        if key not in cases:
            cases[key] = {
                "emails": [],
                "senders": set()
            }

        cases[key]["emails"].append(e)
        cases[key]["senders"].add(sender)

    return list(cases.values())


# =========================
# GPT ENGINE
# =========================

def _build_chain(selected_emails, actor):
    return [
        e for e in selected_emails
        if actor.lower() in (e.get("from") or "").lower()
    ]


def _gpt_analyze_chain(chain, actor):
    text = ""

    for e in chain:
        text += f"""
FROM: {e.get('from')}
SUBJECT: {e.get('subject')}
BODY: {e.get('body','')[:800]}
----------------------
"""

    client = openai.OpenAI()

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Ты юридический аналитик. Возвращай строго структурированный анализ."
            },
            {
                "role": "user",
                "content": f"""
Проанализируй переписку по участнику: {actor}

Верни JSON:
{{
  "summary": "",
  "facts": [],
  "risks": [],
  "obligations": []
}}

Переписка:
{text}
"""
            }
        ]
    )

    return resp.choices[0].message.content


# =========================
# DOCX ENGINE
# =========================

MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _calendar_keyboard(kind: str, year: int | None = None, month: int | None = None):
    now = datetime.now()

    year = int(year or now.year)
    month = int(month or now.month)

    first = datetime(year, month, 1)

    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)

    days_count = (next_month - first).days

    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month_num = month + 1
    next_year = year
    if next_month_num > 12:
        next_month_num = 1
        next_year += 1

    rows = []

    rows.append([
        InlineKeyboardButton(text="◀", callback_data=f"sec:cal:{kind}:{prev_year}:{prev_month}"),
        InlineKeyboardButton(text=f"{MONTH_NAMES_RU.get(month, month)} {year}", callback_data="sec:none"),
        InlineKeyboardButton(text="▶", callback_data=f"sec:cal:{kind}:{next_year}:{next_month_num}"),
    ])

    rows.append([
        InlineKeyboardButton(text="Пн", callback_data="sec:none"),
        InlineKeyboardButton(text="Вт", callback_data="sec:none"),
        InlineKeyboardButton(text="Ср", callback_data="sec:none"),
        InlineKeyboardButton(text="Чт", callback_data="sec:none"),
        InlineKeyboardButton(text="Пт", callback_data="sec:none"),
        InlineKeyboardButton(text="Сб", callback_data="sec:none"),
        InlineKeyboardButton(text="Вс", callback_data="sec:none"),
    ])

    week = []
    first_weekday = first.weekday()

    for _ in range(first_weekday):
        week.append(InlineKeyboardButton(text=" ", callback_data="sec:none"))

    for day in range(1, days_count + 1):
        week.append(
            InlineKeyboardButton(
                text=str(day),
                callback_data=f"sec:date:{kind}:{year}-{month:02d}-{day:02d}",
            )
        )

        if len(week) == 7:
            rows.append(week)
            week = []

    if week:
        while len(week) < 7:
            week.append(InlineKeyboardButton(text=" ", callback_data="sec:none"))
        rows.append(week)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _actor_key(raw_from: str) -> str:

    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*[-–—]\s*(\d{2}\.\d{2}\.\d{4})", t)
    if not m:
        return None

    start = datetime.strptime(m.group(1), "%d.%m.%Y")
    end = datetime.strptime(m.group(2), "%d.%m.%Y")
    return start, end, f"{start.strftime('%d.%m.%Y')}-{end.strftime('%d.%m.%Y')}"


def _actor_key(raw_from: str) -> str:
    raw = raw_from or "unknown"
    match = re.search(r'[\w\.-]+@[\w\.-]+', raw)
    return match.group(0).lower() if match else raw.lower().strip()


def _collect_actors(emails):
    actors = {}

    for e in emails:
        key = _actor_key(e.get("from", ""))
        if key not in actors:
            actors[key] = {
                "key": key,
                "name": e.get("from", key),
                "count": 0
            }
        actors[key]["count"] += 1

    return list(actors.values())


def _make_docx(tasks):
    doc = Document()

    doc.add_heading("Акт выполненных работ", level=1)

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"

    hdr = table.rows[0].cells
    hdr[0].text = "№"
    hdr[1].text = "Задача / вопрос"
    hdr[2].text = "Что сделано"
    hdr[3].text = "Статус"

    for i, task in enumerate(tasks, start=1):
        row = table.add_row().cells
        row[0].text = str(i)
        row[1].text = str(task.get("task", ""))
        row[2].text = str(task.get("done", ""))
        row[3].text = str(task.get("status", ""))

    path = "/tmp/secretary_act.docx"
    doc.save(path)

    return path


def _gpt_make_tasks(emails, project_name, period_text):
    text = ""

    for e in emails:
        text += f"""
FROM: {e.get('from')}
SUBJECT: {e.get('subject')}
BODY: {e.get('body','')[:1200]}
----------------------
"""

    client = openai.OpenAI()

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты юридический секретарь-аналитик. "
                    "Твоя задача — по переписке выделить реальные рабочие задачи, "
                    "кратко описать что было сделано и определить статус."
                )
            },
            {
                "role": "user",
                "content": f"""
Проект/группа: {project_name}
Период: {period_text}

Проанализируй переписку и верни СТРОГО JSON-массив.
Без markdown. Без пояснений.

Формат:
[
  {{
    "task": "краткое описание задачи или вопроса",
    "done": "что сделано за период",
    "status": "закрыто / в работе / требует продолжения"
  }}
]

Переписка:
{text}
"""
            }
        ]
    )

    raw = resp.choices[0].message.content.strip()

    try:
        import json
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    return [
        {
            "task": "Анализ переписки",
            "done": raw,
            "status": "требует проверки"
        }
    ]


async def handle_secretary_callback(cb) -> bool:
    data = cb.data or ""

    if data == "sec:none":
        await cb.answer()
        return True

    chat_id = cb.message.chat.id
    cache = SECRETARY_CACHE.get(chat_id)

    if cache is None:
        await cb.answer("Сценарий секретаря не запущен")
        return True

    if data.startswith("sec:cal:"):
        parts = data.split(":")
        kind = parts[2]
        year = int(parts[3])
        month = int(parts[4])

        title = "Выбери дату начала периода:" if kind == "start" else "Выбери дату окончания периода:"

        await cb.message.edit_text(
            title,
            reply_markup=_calendar_keyboard(kind, year, month),
        )
        await cb.answer()
        return True

    if data.startswith("sec:date:"):
        parts = data.split(":")
        kind = parts[2]
        date_value = parts[3]

        selected_date = datetime.strptime(date_value, "%Y-%m-%d")

        if kind == "start":
            cache["period_start"] = selected_date
            cache["stage"] = "wait_period_end"

            await cb.message.edit_text(
                f"Дата начала: {selected_date.strftime('%d.%m.%Y')}\n\n"
                "Теперь выбери дату окончания периода:",
                reply_markup=_calendar_keyboard("end", selected_date.year, selected_date.month),
            )
            await cb.answer()
            return True

        if kind == "end":
            start = cache.get("period_start")
            if not start:
                await cb.answer("Сначала выбери дату начала")
                return True

            if selected_date < start:
                await cb.answer("Дата окончания раньше даты начала")
                return True

            cache["period_end"] = selected_date
            cache["period_text"] = f"{start.strftime('%d.%m.%Y')}-{selected_date.strftime('%d.%m.%Y')}"
            cache["stage"] = "actor_select"

            await cb.message.edit_text(
                f"Период выбран: {cache['period_text']}\n\n"
                "Читаю почту и собираю адресатов..."
            )

            emails = _fetch_mailru_emails(limit=200)
            cache["emails"] = emails

            actors = _collect_actors(emails)
            cache["actors"] = actors

            if not actors:
                await cb.message.answer("Писем в ящике не нашла.")
                cache["stage"] = "idle"
                await cb.answer()
                return True

            await cb.message.answer(
                "Выбери адресатов, которые относятся к проекту.\n"
                "Пока без кнопок: напиши номера через запятую.\n\n" +
                "\n".join([
                    f"{i+1}. ☐ {a['name']} ({a['count']})"
                    for i, a in enumerate(actors)
                ])
            )

            await cb.answer()
            return True

    await cb.answer()
    return True


async def handle_secretary_message(message, text: str, object_text=None) -> bool:

    if not isinstance(text, str):
        text = str(text)

    raw = text.strip()
    t = raw.lower()

    chat_id = message.chat.id

    cache = SECRETARY_CACHE.get(chat_id)

    if cache is None:
        cache = SECRETARY_CACHE[chat_id] = {
            "stage": "idle",
            "project": "",
            "period_text": "",
            "period_start": None,
            "period_end": None,
            "emails": [],
            "actors": [],
            "selected_actors": [],
            "selected": [],
            "tasks": []
        }

    # -----------------------------
    # START ACT SCENARIO
    # -----------------------------
    m = re.search(r"составь\s+акт\s+по\s+(.+)$", t, flags=re.I)
    if m:
        project = (m.group(1) or "").strip()
        project = project.upper() if project else "ПРОЕКТ"

        cache.clear()
        cache.update({
            "stage": "wait_period_start",
            "project": project,
            "period_text": "",
            "period_start": None,
            "period_end": None,
            "emails": [],
            "actors": [],
            "selected_actors": [],
            "selected": [],
            "tasks": []
        })

        now = datetime.now()

        await message.answer(
            f"Ок. Составляем акт по {project}.\n\n"
            "Выбери дату начала периода:",
            reply_markup=_calendar_keyboard("start", now.year, now.month),
        )
        return True

    # -----------------------------
    # PERIOD INPUT
    # -----------------------------
    if False and cache.get("stage") == "wait_period":
        period = _parse_period(raw)

        if not period:
            await message.answer(
                "Не поняла период. Выбери кнопку или напиши так:\n"
                "01.06.2026-30.06.2026",
                reply_markup=_period_keyboard(),
            )
            return True

        start, end, period_text = period

        cache["period_start"] = start
        cache["period_end"] = end
        cache["period_text"] = period_text
        cache["stage"] = "actor_select"

        emails = _fetch_mailru_emails(limit=200)
        cache["emails"] = emails

        actors = _collect_actors(emails)
        cache["actors"] = actors

        if not actors:
            await message.answer("Писем в ящике не нашла.")
            cache["stage"] = "idle"
            return True

        await message.answer(
            "Выбери адресатов, которые относятся к проекту.\n"
            "Пока без кнопок: напиши номера через запятую.\n\n" +
            "\n".join([
                f"{i+1}. ☐ {a['name']} ({a['count']})"
                for i, a in enumerate(actors)
            ])
        )
        return True

    # -----------------------------
    # ACTOR SELECTION
    # -----------------------------
    if cache.get("stage") == "actor_select":

        nums = [int(x.strip()) for x in t.split(",") if x.strip().isdigit()]

        selected_actors = []
        for n in nums:
            idx = n - 1
            if 0 <= idx < len(cache["actors"]):
                selected_actors.append(cache["actors"][idx])

        if not selected_actors:
            await message.answer("Не выбраны адресаты. Напиши номера через запятую.")
            return True

        cache["selected_actors"] = selected_actors

        selected_keys = {a["key"] for a in selected_actors}
        selected_emails = [
            e for e in cache["emails"]
            if _actor_key(e.get("from", "")) in selected_keys
        ]

        cache["selected"] = selected_emails
        cache["stage"] = "analyzing"

        await message.answer(
            "Адресаты выбраны.\n\n"
            "Запускаю анализ переписки и собираю таблицу акта."
        )

        tasks = _gpt_make_tasks(
            cache["selected"],
            cache.get("project", ""),
            cache.get("period_text", "")
        )

        cache["tasks"] = tasks

        file_path = _make_docx(tasks)

        cache["stage"] = "done"
        cache["doc"] = file_path

        await message.answer(f"Готово. Акт сформирован: {file_path}")
        return True

    # -----------------------------
    # GPT ANALYSIS + DOCX ACT
    # -----------------------------
    if cache.get("stage") == "ready_to_analyze" and ("анализ" in t or "анализируй" in t or "делай" in t):

        await message.answer("Запускаю анализ переписки. Это может занять время.")

        tasks = _gpt_make_tasks(
            cache["selected"],
            cache.get("project", ""),
            cache.get("period_text", "")
        )

        cache["tasks"] = tasks

        file_path = _make_docx(tasks)

        cache["stage"] = "done"
        cache["doc"] = file_path

        await message.answer(f"Готово. Акт сформирован: {file_path}")
        return True

    # -----------------------------
    # FALLBACK
    # -----------------------------
    await message.answer(
        "Секретарь активен.\n\n"
        "Команда для старта:\n"
        "составь акт по РГП"
    )
    return True