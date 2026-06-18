from __future__ import annotations

import re
import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime
import openai
from docx import Document

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

def _make_docx(results):
    doc = Document()

    doc.add_heading("Анализ переписки", level=1)

    for r in results:
        doc.add_heading(r["actor"], level=2)
        doc.add_paragraph(str(r["analysis"]))

    path = "/mnt/data/secretary_report.docx"
    doc.save(path)

    return path


# =========================
# HANDLER
# =========================

async def handle_secretary_message(message, text: str) -> bool:

    raw = (text or "").strip().lower()
    t = raw

    chat_id = message.chat.id

    cache = SECRETARY_CACHE.get(chat_id)

    if cache is None:
        cache = SECRETARY_CACHE[chat_id] = {
            "stage": "idle",
            "emails": [],
            "selected": [],
            "cases": [],
            "actors": [],
            "selected_actors": []
        }

    # -----------------------------
    # MAIL SELECTION
    # -----------------------------
    if cache.get("stage") == "mail_list" and "," in t:

        nums = [int(x.strip()) for x in t.split(",") if x.strip().isdigit()]

        selected = []
        for n in nums:
            idx = n - 1
            if 0 <= idx < len(cache["emails"]):
                selected.append(cache["emails"][idx])

        cache["selected"] = selected
        cache["stage"] = "selected"

        await message.answer("Выбрано писем. Напиши: 'разбери'")
        return True

    # -----------------------------
    # GROUPING → ACTORS
    # -----------------------------
    if cache.get("stage") == "selected" and ("разбери" in t or "дела" in t):

        cases = _build_cases(cache["selected"])
        cache["cases"] = cases

        actors = set()
        for c in cases:
            for s in c["senders"]:
                actors.add(s)

        cache["actors"] = list(actors)
        cache["stage"] = "entity_select"

        await message.answer(
            "Выбери участников:\n" +
            "\n".join([f"{i+1}. {a}" for i, a in enumerate(cache["actors"])])
        )
        return True

    # -----------------------------
    # ENTITY SELECT
    # -----------------------------
    if cache.get("stage") == "entity_select" and "," in t:

        nums = [int(x.strip()) for x in t.split(",") if x.strip().isdigit()]

        selected = []
        for n in nums:
            idx = n - 1
            if 0 <= idx < len(cache["actors"]):
                selected.append(cache["actors"][idx])

        cache["selected_actors"] = selected
        cache["stage"] = "confirmed"

        await message.answer("Ок. Запускаю GPT анализ...")
        return True

    # -----------------------------
    # GPT + DOCX
    # -----------------------------
    if cache.get("stage") == "confirmed" and ("анализ" in t or "gpt" in t):

        results = []

        for actor in cache["selected_actors"]:
            chain = _build_chain(cache["selected"], actor)
            analysis = _gpt_analyze_chain(chain, actor)

            results.append({
                "actor": actor,
                "analysis": analysis
            })

        file_path = _make_docx(results)

        cache["stage"] = "done"
        cache["doc"] = file_path

        await message.answer(f"Готово. DOCX: {file_path}")
        return True

    # -----------------------------
    # FALLBACK
    # -----------------------------
    await message.answer("Команда не распознана")
    return True