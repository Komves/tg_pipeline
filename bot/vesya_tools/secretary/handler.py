from __future__ import annotations

import re
import os
import imaplib
import email
import tempfile
import base64
import quopri
import json
import html as html_lib
from pathlib import Path
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
import openai
from docx import Document
from openpyxl import load_workbook
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




def _imap_connect():
    user = os.getenv("MAILRU_EMAIL", "comves@list.ru")
    password = os.getenv("MAILRU_APP_PASSWORD")

    if not password:
        return None

    imap = imaplib.IMAP4_SSL("imap.mail.ru", 993)
    imap.login(user, password)
    return imap

def _imap_select_mailbox(imap, mailbox):
    mailbox = str(mailbox or "INBOX")

    for candidate in (mailbox, f'"{mailbox}"'):
        try:
            status, _ = imap.select(candidate)
            if status == "OK":
                return True
        except Exception:
            continue

    return False


def _parse_imap_list_name(raw_line):
    if isinstance(raw_line, bytes):
        line = raw_line.decode("utf-8", errors="ignore")
    else:
        line = str(raw_line)

    m = re.search(r'\) "[^"]*" (.+)$', line)
    if not m:
        return ""

    name = m.group(1).strip()

    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]

    return name.strip()


def _mailru_target_folders(imap):
    folders = [
        {
            "mailbox": "INBOX",
            "folder": "INBOX",
        }
    ]

    try:
        status, data = imap.list()

        if status == "OK":
            for raw in data or []:
                line = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
                name = _parse_imap_list_name(line)

                if not name:
                    continue

                low = line.lower()

                print(f"[secretary] mailbox found: {line}", flush=True)

                if (
                    "\\sent" in low
                    or "sent" in low
                    or "отправ" in low
                    or "&bb4" in low
                ):
                    folders.append({
                        "mailbox": name,
                        "folder": "Sent",
                    })

    except Exception as e:
        print(f"[secretary] mailbox list failed: {type(e).__name__}: {e}", flush=True)

    folders.append({
        "mailbox": "Sent",
        "folder": "Sent",
    })

    unique = []
    seen = set()

    for f in folders:
        key = f"{f.get('mailbox')}|{f.get('folder')}"
        if key not in seen:
            unique.append(f)
            seen.add(key)

    return unique

def _parse_email_date(value):
    try:
        dt = parsedate_to_datetime(value or "")
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _fetch_mailru_headers(limit=300, period_start=None, period_end=None):
    imap = _imap_connect()
    if imap is None:
        return []

    results = []

    start_dt = period_start
    end_dt = period_end

    if end_dt:
        end_dt = end_dt + timedelta(days=1)

    try:
        for folder_info in _mailru_target_folders(imap):
            try:
                mailbox = folder_info["mailbox"]
                folder = folder_info["folder"]

                if not _imap_select_mailbox(imap, mailbox):
                    print(f"[secretary] cannot select mailbox={mailbox}", flush=True)
                    continue
                status, data = imap.search(None, "ALL")

                if status != "OK" or not data or not data[0]:
                    continue

                ids = data[0].split()[-limit:]

                for num in ids:
                    status, msg_data = imap.fetch(
                        num,
                        "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES)])"
                    )

                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_headers = msg_data[0][1]
                    msg = email.message_from_bytes(raw_headers)

                    date_raw = _decode_mime(msg.get("Date"))
                    msg_dt = _parse_email_date(date_raw)

                    if start_dt and msg_dt and msg_dt < start_dt:
                        continue

                    if end_dt and msg_dt and msg_dt >= end_dt:
                        continue

                    results.append({
                        "folder": folder,
                        "imap_folder": mailbox,
                        "imap_id": num.decode(errors="ignore"),
                        "date": date_raw,
                        "date_iso": msg_dt.isoformat() if msg_dt else "",
                        "from": _decode_mime(msg.get("From")),
                        "to": _decode_mime(msg.get("To")),
                        "subject": _decode_mime(msg.get("Subject")),
                        "message_id": _decode_mime(msg.get("Message-ID")),
                        "in_reply_to": _decode_mime(msg.get("In-Reply-To")),
                        "references": _decode_mime(msg.get("References")),
                        "attachments": [],
                        "body": "",
                    })

            except Exception as e:
                print(f"[secretary] header fetch folder failed {folder}: {type(e).__name__}: {e}", flush=True)
                continue

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def _imap_unquote(value):
    if value is None:
        return ""

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")

    value = str(value).strip()

    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]

    return value


def _decode_transfer_payload(raw_bytes, encoding):
    encoding = (encoding or "").lower().strip()

    if not raw_bytes:
        return b""

    if encoding == "base64":
        return base64.b64decode(raw_bytes)

    if encoding in ("quoted-printable", "quotedprintable"):
        return quopri.decodestring(raw_bytes)

    return raw_bytes


def _extract_fetch_bytes(msg_data):
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def _tokenize_bodystructure(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")

    tokens = []
    i = 0

    while i < len(raw):
        c = raw[i]

        if c.isspace():
            i += 1
            continue

        if c in "()":
            tokens.append(c)
            i += 1
            continue

        if c == '"':
            i += 1
            buf = ""

            while i < len(raw):
                if raw[i] == "\\" and i + 1 < len(raw):
                    buf += raw[i + 1]
                    i += 2
                    continue

                if raw[i] == '"':
                    i += 1
                    break

                buf += raw[i]
                i += 1

            tokens.append(buf)
            continue

        j = i
        while j < len(raw) and not raw[j].isspace() and raw[j] not in "()":
            j += 1

        tokens.append(raw[i:j])
        i = j

    return tokens


def _parse_bodystructure_tokens(tokens):
    pos = 0

    def parse():
        nonlocal pos

        if pos >= len(tokens):
            return None

        token = tokens[pos]

        if token == "(":
            pos += 1
            arr = []

            while pos < len(tokens) and tokens[pos] != ")":
                arr.append(parse())

            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1

            return arr

        pos += 1
        return token

    return parse()


def _find_param_value(params, name):
    if not isinstance(params, list):
        return ""

    name = (name or "").lower()

    for i in range(0, len(params) - 1, 2):
        if str(params[i]).lower() == name:
            return _decode_mime(_imap_unquote(params[i + 1]))

    return ""


def _walk_bodystructure(node, prefix=""):
    parts = []

    if not isinstance(node, list) or not node:
        return parts

    if isinstance(node[0], list):
        child_index = 1

        for child in node:
            if isinstance(child, list):
                part_no = f"{prefix}.{child_index}" if prefix else str(child_index)
                parts.extend(_walk_bodystructure(child, part_no))
                child_index += 1

        return parts

    if len(node) < 7:
        return parts

    maintype = str(node[0] or "").lower()
    subtype = str(node[1] or "").lower()
    params = node[2] if len(node) > 2 else []
    encoding = str(node[5] or "").lower() if len(node) > 5 else ""
    size = node[6] if len(node) > 6 else ""

    disposition = None
    disposition_params = []

    for item in node[7:]:
        if isinstance(item, list) and item:
            first = str(item[0] or "").lower()
            if first in ("attachment", "inline"):
                disposition = first
                if len(item) > 1 and isinstance(item[1], list):
                    disposition_params = item[1]

    filename = (
        _find_param_value(disposition_params, "filename")
        or _find_param_value(params, "name")
    )

    parts.append({
        "part_no": prefix or "1",
        "maintype": maintype,
        "subtype": subtype,
        "content_type": f"{maintype}/{subtype}",
        "encoding": encoding,
        "size": str(size),
        "disposition": disposition or "",
        "filename": filename or "",
    })

    return parts


def _fetch_bodystructure(imap, imap_id):
    status, data = imap.fetch(str(imap_id).encode(), "(BODYSTRUCTURE)")

    if status != "OK" or not data:
        return []

    raw = ""

    for item in data:
        if isinstance(item, tuple) and item:
            raw = item[0].decode("utf-8", errors="ignore")
            break

    marker = "BODYSTRUCTURE "
    if marker in raw:
        raw = raw.split(marker, 1)[1].strip()

    if raw.endswith(")"):
        raw = raw[:-1].strip()

    tokens = _tokenize_bodystructure(raw)
    tree = _parse_bodystructure_tokens(tokens)

    return _walk_bodystructure(tree)


def _read_excel_preview(path, max_rows=20, max_cols=8):
    out = []

    try:
        wb = load_workbook(path, read_only=True, data_only=True)

        for ws in wb.worksheets[:3]:
            out.append(f"Лист: {ws.title}")

            rows_added = 0

            for row in ws.iter_rows(max_row=max_rows, max_col=max_cols, values_only=True):
                values = ["" if v is None else str(v) for v in row]

                if any(v.strip() for v in values):
                    out.append(" | ".join(values))
                    rows_added += 1

                if rows_added >= max_rows:
                    break

            out.append("")

        wb.close()

    except Exception as e:
        out.append(f"Excel не удалось прочитать: {type(e).__name__}: {e}")

    return "\n".join(out).strip()[:5000]


def _fetch_part_bytes(imap, imap_id, part_no):
    status, data = imap.fetch(
        str(imap_id).encode(),
        f"(BODY.PEEK[{part_no}])"
    )

    if status != "OK":
        return b""

    return _extract_fetch_bytes(data)


def _html_to_text(value):
    text = value or ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_message_text_and_attachments(msg, imap_id):
    body = ""
    html_body = ""
    attachments = []
    excel_texts = []

    for part in msg.walk():
        
        content_type = (part.get_content_type() or "").lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = _decode_mime(part.get_filename() or "")

        payload = part.get_payload(decode=True)

        charset = part.get_content_charset() or "utf-8"

        if content_type == "text/plain" and not filename and not body:
            try:
                body = (payload or b"").decode(charset, errors="ignore")
            except Exception:
                body = (payload or b"").decode("utf-8", errors="ignore")

        elif content_type == "text/html" and not filename and not html_body:
            try:
                html_body = (payload or b"").decode(charset, errors="ignore")
            except Exception:
                html_body = (payload or b"").decode("utf-8", errors="ignore")

        if filename:
            item = {
                "filename": filename,
                "content_type": content_type,
                "size": str(len(payload or b"")),
            }

            lower_name = filename.lower()

            if lower_name.endswith(".xlsx") and payload:
                safe_name = re.sub(r"[^a-zA-Zа-яА-Я0-9_.-]+", "_", filename)
                tmp_path = Path(tempfile.gettempdir()) / f"sec_{imap_id}_{safe_name}"

                with open(tmp_path, "wb") as f:
                    f.write(payload)

                preview = _read_excel_preview(tmp_path)

                item["excel_preview"] = preview
                excel_texts.append(
                    f"Файл: {filename}\n{preview}"
                )

                try:
                    tmp_path.unlink()
                except Exception:
                    pass

            elif lower_name.endswith(".xls"):
                item["excel_preview"] = "Формат .xls пока не читаю без xlrd. В акт пойдет имя вложения."

            attachments.append(item)

    if not body and html_body:
        body = _html_to_text(html_body)

    body = (body or "").strip()

    if excel_texts:
        body = (
            body
            + "\n\nEXCEL PREVIEW:\n"
            + "\n\n".join(excel_texts)
        ).strip()

    return body[:9000], attachments


def _fetch_mailru_full_messages(headers, max_messages=50):
    imap = _imap_connect()
    if imap is None:
        return []

    results = []

    try:
        for h in headers[:max_messages]:
            folder = h.get("folder")
            mailbox = h.get("imap_folder") or folder
            imap_id = h.get("imap_id")

            if not folder or not mailbox or not imap_id:
                continue

            try:
                if not _imap_select_mailbox(imap, mailbox):
                    print(f"[secretary] cannot select mailbox={mailbox}", flush=True)
                    continue

                status, msg_data = imap.fetch(
                    str(imap_id).encode(),
                    "(RFC822)"
                )

                if status != "OK" or not msg_data:
                    continue

                raw_bytes = _extract_fetch_bytes(msg_data)

                if not raw_bytes:
                    print(
                        f"[secretary] RFC822 empty folder={folder} id={imap_id}",
                        flush=True
                    )
                    continue

                msg = email.message_from_bytes(raw_bytes)

                body, attachments = _extract_message_text_and_attachments(msg, imap_id)

                item = dict(h)
                item["body"] = body
                item["attachments"] = attachments

                results.append(item)

                print(
                    f"[secretary] full loaded RFC822 folder={folder} id={imap_id} "
                    f"attachments={len(attachments)} body_chars={len(body)}",
                    flush=True
                )

            except Exception as e:
                print(
                    f"[secretary] full fetch RFC822 failed folder={folder} id={imap_id}: "
                    f"{type(e).__name__}: {e}",
                    flush=True
                )
                continue

    finally:
        try:
            imap.logout()
        except Exception:
            pass

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

        key = sender

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
FOLDER: {e.get('folder')}
DATE: {e.get('date')}
FROM: {e.get('from')}
TO: {e.get('to')}
SUBJECT: {e.get('subject')}
ATTACHMENTS: {", ".join([a.get("filename", "") for a in (e.get("attachments") or [])])}
BODY: {e.get('body','')[:2000]}
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
    raw = raw_from or "unknown"
    match = re.search(r'[\w\.-]+@[\w\.-]+', raw)
    return match.group(0).lower() if match else raw.lower().strip()

def _norm_msg_id(value: str) -> str:
    return (value or "").strip().lower().strip("<>")


def _header_refs(header: dict) -> set[str]:
    refs = set()

    for field in ("message_id", "in_reply_to", "references"):
        raw = str(header.get(field) or "")
        for item in re.findall(r"<([^>]+)>", raw):
            item = _norm_msg_id(item)
            if item:
                refs.add(item)

    return refs


def _expand_headers_by_threads(all_headers, seed_headers):
    selected = list(seed_headers or [])
    selected_ids = set()

    for h in selected:
        selected_ids.update(_header_refs(h))

    changed = True

    while changed:
        changed = False

        for h in all_headers:
            if h in selected:
                continue

            refs = _header_refs(h)

            if refs and selected_ids and refs.intersection(selected_ids):
                selected.append(h)
                selected_ids.update(refs)
                changed = True

    return selected


def _norm_subject(value: str) -> str:
    s = (value or "").lower().strip()
    s = re.sub(r"^\s*(re|fw|fwd|ответ|пересл):\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _email_dt(e: dict) -> datetime:
    iso = e.get("date_iso") or ""
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return datetime.min


def _email_participants(e: dict) -> set[str]:
    out = set()

    for field in ("from", "to"):
        raw = e.get(field) or ""
        for m in re.findall(r'[\w\.-]+@[\w\.-]+', raw):
            out.add(m.lower())

    return out


def _build_mail_threads(emails):
    def norm_subject(s: str):
        s = (s or "").lower().strip()
        s = re.sub(r"^(re|fw|fwd|ответ|пересл):\s*", "", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def dt(e):
        try:
            return datetime.fromisoformat(e.get("date_iso") or "")
        except Exception:
            return datetime.min

    def participants(e):
        s = set()
        for k in ("from", "to"):
            raw = e.get(k) or ""
            for m in re.findall(r'[\w\.-]+@[\w\.-]+', raw):
                s.add(m.lower())
        return s

    indexed = []
    for i, e in enumerate(emails, start=1):
        e = dict(e)
        e["email_no"] = i
        e["_msg"] = _norm_msg_id(e.get("message_id"))
        e["_refs"] = _header_refs(e)
        e["_sub"] = norm_subject(e.get("subject"))
        e["_dt"] = dt(e)
        e["_part"] = participants(e)
        indexed.append(e)

    indexed.sort(key=lambda x: x["_dt"])

    # --- UNION FIND ---
    parent = list(range(len(indexed)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) Message-ID linking
    msg_map = {}
    for i, e in enumerate(indexed):
        if e["_msg"]:
            msg_map[e["_msg"]] = i

    for i, e in enumerate(indexed):
        for r in e["_refs"]:
            if r in msg_map:
                union(i, msg_map[r])

    # 2) Subject + participants + time window (NEW INTELLIGENCE LAYER)
    for i in range(len(indexed)):
        for j in range(i + 1, len(indexed)):

            a, b = indexed[i], indexed[j]

            if not a["_sub"] or not b["_sub"]:
                continue

            # subject must match
            if a["_sub"] != b["_sub"]:
                continue

            # at least one participant overlap
            if not (a["_part"] & b["_part"]):
                continue

            # time window 21 days (important fix vs 14)
            if a["_dt"] == datetime.min or b["_dt"] == datetime.min:
                continue

            if abs((b["_dt"] - a["_dt"]).days) <= 21:
                union(i, j)

    # --- BUILD THREADS ---
    groups = {}
    for i in range(len(indexed)):
        root = find(i)
        groups.setdefault(root, []).append(indexed[i])

    threads = []
    for k, msgs in enumerate(groups.values(), start=1):
        msgs.sort(key=lambda x: x["_dt"])

        threads.append({
            "thread_no": k,
            "subject": msgs[0].get("_sub", ""),
            "participants": list({p for m in msgs for p in m["_part"]}),
            "messages": msgs,
            "inbox_count": sum(1 for m in msgs if m.get("folder") == "INBOX"),
            "sent_count": sum(1 for m in msgs if m.get("folder") == "Sent"),
        })

    threads.sort(key=lambda t: t["messages"][0]["_dt"] if t["messages"] else datetime.min)

    for i, t in enumerate(threads, start=1):
        t["thread_no"] = i

    return threads

def _thread_to_text(thread):
    text = ""

    for e in thread.get("messages") or []:
        text += f"""
EMAIL #{e.get('email_no')}
THREAD #{thread.get('thread_no')}
FOLDER: {e.get('folder')}
DATE: {e.get('date')}
FROM: {e.get('from')}
TO: {e.get('to')}
SUBJECT: {e.get('subject')}
MESSAGE-ID: {e.get('message_id')}
IN-REPLY-TO: {e.get('in_reply_to')}
REFERENCES: {e.get('references')}
ATTACHMENTS: {", ".join([
    (
        a.get("filename", "")
        + (f" ({a.get('content_type', '')}, size={a.get('size', '')})" if a.get("size") else "")
    )
    for a in (e.get("attachments") or [])
])}
BODY: {e.get('body','')[:2500]}
----------------------
"""

    return text.strip()


def _safe_json_loads(raw):
    raw = (raw or "").strip()

    raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


def _gpt_analyze_thread(thread, project_name, period_text):
    client = openai.OpenAI()

    SYSTEM_PROMPT = """
Ты анализируешь ОДНУ ЦЕПОЧКУ ДЕЛОВОЙ РАБОТЫ.

Не пересказывай письма.
Восстанавливай реальный процесс работы.

Всегда выделяй:
- кто инициировал запрос
- что конкретно хотели
- что сделал Марголин
- что было отправлено или подготовлено
- чем завершилось

ВАЖНО:
- FOLDER=INBOX = входящий запрос
- FOLDER=Sent = действие пользователя (работа)

Если есть Sent — это ОСНОВНОЙ результат работы, не игнорируй его.
"""

    text = _thread_to_text(thread)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": f"""
Проект/группа: {project_name}
Период: {period_text}

Верни СТРОГО JSON-объект.

Формат:
{{
  "thread_no": {thread.get("thread_no")},
  "task": "короткое название задачи",
  "request_from": "",
  "request_content": "",
  "user_action": "",
  "result": "",
  "status": "в работе",
  "emails": [],
  "needs_manual_check": false
}}

Цепочка:
{text}
"""
            }
        ]
    )

    raw = resp.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except Exception:
        return {
            "thread_no": thread.get("thread_no"),
            "task": "parse_error",
            "raw": raw,
            "needs_manual_check": True
        }


def _gpt_make_tasks_from_threads(thread_summaries, project_name, period_text):
    client = openai.OpenAI()

    SYSTEM_PROMPT = """
Ты формируешь АКТ ВЫПОЛНЕННЫХ РАБОТ.

Ты НЕ пересказываешь письма.
Ты объединяешь уже разобранные цепочки в задачи.

Каждая задача = реальный кейс работы:
1. инициатор
2. запрос
3. действия Марголина
4. результат
5. итог

Запрещено:
- "получено письмо"
- "требуется проверка" без содержания
- общие формулировки
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": f"""
Проект: {project_name}
Период: {period_text}

Вот уже разобранные цепочки:

{json.dumps(thread_summaries, ensure_ascii=False, indent=2)}

Верни СТРОГО JSON-массив:

[
  {{
    "task": "название задачи",
    "done": "инициатор → запрос → действия → результат → итог",
    "status": "в работе",
    "emails": [],
    "threads": []
  }}
]

Правила:
- done минимум 3-5 предложений
- обязательно инициатор
- обязательно действие Марголина
- обязательно результат
"""
            }
        ]
    )

    raw = resp.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except Exception:
        return [
            {
                "task": "parse_error",
                "done": raw,
                "status": "error"
            }
        ]



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


def _tasks_review_text(tasks):
    lines = ["GPT сгруппировал задачи. Проверь статусы:\n"]
    

    for i, task in enumerate(tasks, start=1):
        emails = task.get("emails") or []
        emails_text = ", ".join(str(x) for x in emails) if emails else "не указаны"

        lines.append(
            f"{i}. {task.get('task', '')}\n"
            f"   Что сделано: {task.get('done', '')}\n"
            f"   Письма: {emails_text}\n"
            f"   Статус: {task.get('status', 'в работе')}\n"
        )

    lines.append("Когда статусы верные — нажми «Сформировать акт».")
    return "\n".join(lines)


def _tasks_review_keyboard(tasks):
    rows = []

    for i, task in enumerate(tasks):
        n = i + 1
        rows.append([
            InlineKeyboardButton(text=f"{n}: закрыто", callback_data=f"sec:status:{i}:закрыто"),
            InlineKeyboardButton(text=f"{n}: в работе", callback_data=f"sec:status:{i}:в работе"),
        ])
        rows.append([
            InlineKeyboardButton(text=f"{n}: убрать", callback_data=f"sec:status:{i}:убрать"),
        ])

    rows.append([
        InlineKeyboardButton(text="Сформировать акт", callback_data="sec:act:make")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

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

    for i, e in enumerate(emails, start=1):
        text += f"""
EMAIL #{i}
FOLDER: {e.get('folder')}
DATE: {e.get('date')}
FROM: {e.get('from')}
TO: {e.get('to')}
SUBJECT: {e.get('subject')}
ATTACHMENTS: {", ".join([
    (
        a.get("filename", "")
        + (f" ({a.get('content_type', '')}, size={a.get('size', '')})" if a.get("size") else "")
    )
    for a in (e.get("attachments") or [])
])}
BODY: {e.get('body','')[:2000]}
----------------------
"""

    client = openai.OpenAI()

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты формируешь АКТ ВЫПОЛНЕННЫХ РАБОТ.\n"
                    "\n"
                    "НЕ пересказываешь письма.\n"
                    "Описываешь реальные действия Марголина.\n"
                    "\n"
                    "Каждая задача = мини-кейс:\n"
                    "1. инициатор\n"
                    "2. что хотели\n"
                    "3. что сделал Марголин\n"
                    "4. что отправлено/подготовлено\n"
                    "5. итог\n"
                    "\n"
                    "Запрещено:\n"
                    "- 'получено письмо'\n"
                    "- 'требуется проверка' без содержания\n"
                    "- пустые формулировки\n"
                )
            },
            {
                "role": "user",
                "content": f"""
Проект/группа: {project_name}
Период: {period_text}

"Верни СТРОГО JSON-массив. Без markdown. Без пояснений."

Формат:

Правила:
1. FOLDER=INBOX означает: пользователь получил письмо/документы/запрос.
2. FOLDER=Sent означает: пользователь сам отправил ответ/анализ/проект/таблицу/документы.
3. Поле done пиши не куцо, а содержательно: «получен запрос по вопросу ... с приложением ...; подготовлен и направлен ответ/анализ/проект ..., в котором отражено ...».
4. НЕ пиши обезличенно «запрошены дополнительные сведения», если не можешь указать у кого и по какому вопросу.
5. НЕ пиши, что заявление подано или отправлено пользователем, если переписка про анализ/ответ/защиту от такого заявления.
6. Не дроби одну смысловую задачу на несколько строк только из-за разных писем.
7. Не склеивай разные задачи только из-за одного отправителя.
8. Если по письмам непонятно, что именно сделано, пиши в done: «требует ручной проверки: ...».
9. Всегда указывай status = "в работе". Пользователь потом сам поменяет статус.
10. В emails укажи номера писем EMAIL #, на которых основана задача.

Переписка:
{text}
"""
            }
        ]
    )

    raw = resp.choices[0].message.content.strip()

    with open("/tmp/gpt_answer.txt", "w", encoding="utf-8") as f:
        f.write(raw)

    print("GPT ANSWER SAVED: /tmp/gpt_answer.txt", flush=True)

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

    if data.startswith("sec:status:"):
        parts = data.split(":")
        idx = int(parts[2])
        status = parts[3]

        tasks = cache.get("tasks") or []

        if 0 <= idx < len(tasks):
            if status == "убрать":
                tasks[idx]["exclude"] = True
                tasks[idx]["status"] = "убрать из акта"
            else:
                tasks[idx]["exclude"] = False
                tasks[idx]["status"] = status

            cache["tasks"] = tasks

            try:
                await cb.message.edit_text(
                    _tasks_review_text(tasks),
                    reply_markup=_tasks_review_keyboard(tasks),
                )
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    raise

        await cb.answer()
        return True

    if data == "sec:act:make":
        tasks = [
            t for t in (cache.get("tasks") or [])
            if not t.get("exclude")
        ]

        file_path = _make_docx(tasks)

        cache["stage"] = "done"
        cache["doc"] = file_path

        from aiogram.types import FSInputFile

        await cb.message.answer("Готово. Акт сформирован.")

        await cb.message.answer_document(
            FSInputFile(file_path),
            caption="Проект акта выполненных работ"
        )

        await cb.answer()
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

            emails = _fetch_mailru_headers(
                limit=300,
                period_start=cache.get("period_start"),
                period_end=cache.get("period_end"),
            )
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

        emails = _fetch_mailru_headers(limit=50)
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
        selected_headers = [
            e for e in cache["emails"]
            if (
                _actor_key(e.get("from", "")) in selected_keys
                or any(key in (e.get("to", "") or "").lower() for key in selected_keys)
            )
        ]

        selected_headers = _expand_headers_by_threads(
            cache["emails"],
            selected_headers,
        )

        await message.answer(
            f"Выбрано писем: {len(selected_headers)}.\n"
            "Теперь читаю полные письма и вложения по выбранным адресатам..."
        )

        selected_emails = _fetch_mailru_full_messages(
            selected_headers,
            max_messages=50
        )

        cache["selected"] = selected_emails
        cache["stage"] = "analyzing"

        await message.answer(
            "Адресаты выбраны.\n\n"
            "Запускаю анализ переписки и собираю таблицу акта."
        )

        print("\n========== SECRETARY DEBUG ==========", flush=True)
        print(f"Всего писем: {len(cache['emails'])}", flush=True)
        print(f"Выбрано адресатов: {len(selected_actors)}", flush=True)
        print(f"После фильтра осталось писем: {len(selected_emails)}", flush=True)

        total_chars = sum(len(e.get("body", "")) for e in selected_emails)
        print(f"Всего символов для GPT: {total_chars}", flush=True)

        import json

        with open("/tmp/raw_selected_emails.json", "w", encoding="utf-8") as f:
            json.dump(
                selected_emails,
                f,
                ensure_ascii=False,
                indent=2,
            )

        print("DEBUG FILE: /tmp/raw_selected_emails.json", flush=True)

        threads = _build_mail_threads(cache["selected"])
        cache["threads"] = threads

        thread_summaries = []

        for thread in threads:
            summary = _gpt_analyze_thread(
                thread,
                cache.get("project", ""),
                cache.get("period_text", "")
            )
            thread_summaries.append(summary)

        cache["thread_summaries"] = thread_summaries

        tasks = _gpt_make_tasks_from_threads(
            thread_summaries,
            cache.get("project", ""),
            cache.get("period_text", "")
        )

        cache["tasks"] = tasks

        cache["stage"] = "review_tasks"

        await message.answer(
            _tasks_review_text(tasks),
            reply_markup=_tasks_review_keyboard(tasks),
        )

        return True

    # -----------------------------
    # GPT ANALYSIS + DOCX ACT
    # -----------------------------
    if cache.get("stage") == "ready_to_analyze" and ("анализ" in t or "анализируй" in t or "делай" in t):

        await message.answer("Запускаю анализ переписки. Это может занять время.")

        threads = _build_mail_threads(cache["selected"])
        cache["threads"] = threads

        with open("/tmp/secretary_threads.json", "w", encoding="utf-8") as f:
            json.dump(
                threads,
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        print(f"Построено цепочек: {len(threads)}", flush=True)
        print("DEBUG FILE: /tmp/secretary_threads.json", flush=True)

        thread_summaries = []

        for thread in threads:
            summary = _gpt_analyze_thread(
                thread,
                cache.get("project", ""),
                cache.get("period_text", "")
            )
            thread_summaries.append(summary)

        cache["thread_summaries"] = thread_summaries

        with open("/tmp/secretary_thread_summaries.json", "w", encoding="utf-8") as f:
            json.dump(
                thread_summaries,
                f,
                ensure_ascii=False,
                indent=2,
            )

        tasks = _gpt_make_tasks_from_threads(
            thread_summaries,
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