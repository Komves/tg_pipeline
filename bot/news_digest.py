# bot/news_digest.py
from __future__ import annotations

import os
import re
import json
import time
import html
import hashlib
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from openai import OpenAI


# ===== storage =====
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
NEWS_SEEN_TSV = DATA_DIR / "news_seen.tsv"

RETENTION_DAYS = int(os.getenv("NEWS_RETENTION_DAYS", "3"))

# ===== OpenAI =====
DEFAULT_MODEL = os.getenv("NEWS_OPENAI_MODEL", os.getenv("C_OPENAI_MODEL", "gpt-5"))
MAX_TRIES = int(os.getenv("NEWS_OPENAI_TRIES", "2"))
SLEEP_BETWEEN_TRIES_SEC = float(os.getenv("NEWS_OPENAI_RETRY_SLEEP", "1.0"))

# ===== Telethon =====
_session_env = (os.getenv("TG_SESSION", "tg_session") or "tg_session").strip()
if _session_env.startswith("/"):
    SESSION_BASE = _session_env
else:
    SESSION_BASE = str(DATA_DIR / _session_env)

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_WS_RE = re.compile(r"\s+")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _is_text_only_links(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    if not t:
        return True

    no_urls = _URL_RE.sub(" ", t)
    alnum = re.sub(
        r"[^0-9A-Za-zА-Яа-яЁёЇїІіЄєҐґ\u00C0-\u024F\u1E00-\u1EFF]",
        "",
        no_urls,
    )
    return len(alnum) == 0


def _ensure_seen_header() -> None:
    if not NEWS_SEEN_TSV.exists():
        NEWS_SEEN_TSV.write_text("ts_utc\tevent_id\turl\ttitle\n", encoding="utf-8")


def _url_hash(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:16]


def _norm_event_text(s: str) -> str:
    if not s:
        return ""
    t = s.lower()
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"[^0-9a-zа-яёіїєґ\s]", " ", t, flags=re.IGNORECASE)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _event_id_from_title_summary(title: str, summary: str) -> str:
    base = (_norm_event_text(title) + " | " + _norm_event_text(summary)).strip()
    if not base:
        base = "empty"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _load_seen_sets() -> tuple[set[str], set[str]]:
    if not NEWS_SEEN_TSV.exists():
        return set(), set()

    seen_event: set[str] = set()
    seen_url: set[str] = set()

    for line in NEWS_SEEN_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ts_utc"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            seen_event.add(parts[1])
        if len(parts) >= 3:
            seen_url.add(_url_hash(parts[2]))

    return seen_event, seen_url


def _append_seen(event_id: str, url: str, title: str) -> None:
    _ensure_seen_header()
    ts = _now_utc().isoformat()
    with NEWS_SEEN_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{event_id}\t{url}\t{title}\n")


def load_news_sources(path: Path) -> List[str]:
    if not path.exists():
        raise RuntimeError(f"news_sources.txt not found: {path}")
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _post_url(username: str, msg_id: int) -> str:
    return f"https://t.me/{username}/{msg_id}"


def _build_prompt(posts: List[Dict[str, str]], limit: int) -> str:
    payload = [{"url": p["url"], "text": p["text"]} for p in posts[:2000]]
    return (
        f"Ты — редактор новостей. Выбери максимум {limit} главных новостей.\n"
        f"Верни JSON массив: "
        f'[{{"title":"...","summary":"...","url":"..."}}]\n'
        f"Пиши на русском.\n\n"
        f"Посты:\n{json.dumps(payload, ensure_ascii=False)}"
    )


# ===== FIXED OPENAI CALL =====
def _call_openai(posts: List[Dict[str, str]], limit: int) -> List[dict]:
    client = OpenAI()

    prompt = _build_prompt(posts, limit)

    resp = client.responses.create(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": prompt}],
    )

    text = (getattr(resp, "output_text", "") or "").strip()

    print(
        f"[news_digest] model={DEFAULT_MODEL} out_head={text[:200].replace(chr(10),' ')}",
        flush=True,
    )

    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    return []


@dataclass(frozen=True)
class DigestItem:
    event_id: str
    title: str
    summary: str
    url: str


async def fetch_posts_last_hours(sources: List[str], hours: int) -> List[Dict[str, str]]:
    cutoff = _now_utc() - timedelta(hours=hours)
    out: List[Dict[str, str]] = []

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()

    try:
        for src in sources:
            try:
                entity = await client.get_entity(src)
                username = entity.username
                if not username:
                    continue

                async for msg in client.iter_messages(entity, offset_date=cutoff, reverse=True):
                    text = (msg.message or "").strip()
                    if not text:
                        continue
                    if _is_text_only_links(text):
                        continue

                    out.append(
                        {
                            "url": _post_url(username, msg.id),
                            "text": text,
                        }
                    )

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception:
                continue

    finally:
        await client.disconnect()

    return out


def build_html_message(items: List[DigestItem], *, hours: int) -> str:
    if not items:
        return f"Новых значимых новостей за последние {hours} часов нет."

    lines = [f"📰 Главные новости за последние {hours} часов", ""]

    for i, it in enumerate(items, 1):
        lines.append(f'{i}. <a href="{it.url}">{html.escape(it.title)}</a>')
        if it.summary:
            lines.append(f"— {html.escape(it.summary)}")
        lines.append("")

    return "\n".join(lines)


async def get_news_digest(*, news_sources_path: Path, hours: int = 12, limit: int = 10) -> List[DigestItem]:
    seen_event_ids, seen_url_ids = _load_seen_sets()

    sources = load_news_sources(news_sources_path)
    posts = await fetch_posts_last_hours(sources, hours)

    if not posts:
        return []

    data = _call_openai(posts, limit)

    out: List[DigestItem] = []

    for x in data:
        title = x.get("ti
