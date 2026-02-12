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
    """
    True если текст фактически состоит только из ссылок/пробелов/пунктуации.
    Такие посты игнорируем.
    """
    if not text:
        return True
    t = text.strip()
    if not t:
        return True

    no_urls = _URL_RE.sub(" ", t)
    # оставить только буквы/цифры
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
    u = (url or "").strip()
    return hashlib.sha1(u.encode("utf-8")).hexdigest()[:16]


def _norm_event_text(s: str) -> str:
    """
    Нормализация "смысла" события для стабильного event_id:
    - lower
    - убираем URL
    - убираем лишние символы (оставляем буквы/цифры/пробел)
    - схлопываем пробелы
    """
    if not s:
        return ""
    t = s.lower()
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"[^0-9a-zа-яёіїєґ\u00C0-\u024F\u1E00-\u1EFF\s]", " ", t, flags=re.IGNORECASE)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _event_id_from_title_summary(title: str, summary: str) -> str:
    base = (_norm_event_text(title) + " | " + _norm_event_text(summary)).strip()
    if not base:
        base = _norm_event_text(title) or _norm_event_text(summary)
    if not base:
        base = "empty"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _load_seen_sets() -> tuple[set[str], set[str]]:
    """
    Возвращает два множества:
      - seen_event_ids: чтобы не повторять СОБЫТИЯ
      - seen_url_ids: чтобы не повторять ТОЧНО ТЕ ЖЕ ПОСТЫ (back-compat)
    """
    if not NEWS_SEEN_TSV.exists():
        return set(), set()

    seen_event: set[str] = set()
    seen_url: set[str] = set()

    for line in NEWS_SEEN_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ts_utc"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        eid = (parts[1] or "").strip()
        if eid:
            seen_event.add(eid)
        if len(parts) >= 3:
            u = (parts[2] or "").strip()
            if u:
                seen_url.add(_url_hash(u))

    return seen_event, seen_url


def _append_seen(event_id: str, url: str, title: str) -> None:
    _ensure_seen_header()
    ts = _now_utc().isoformat()
    event_id = (event_id or "").strip()
    if not event_id:
        return
    url = (url or "").replace("\t", " ").strip()
    title = (title or "").replace("\t", " ").strip()
    with NEWS_SEEN_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{event_id}\t{url}\t{title}\n")


def _prune_seen(retention_days: int) -> None:
    if not NEWS_SEEN_TSV.exists():
        return
    cut = _now_utc() - timedelta(days=max(1, int(retention_days)))

    lines = NEWS_SEEN_TSV.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    header = lines[0] if lines[0].startswith("ts_utc") else "ts_utc\tevent_id\turl\ttitle"
    kept: List[str] = [header]

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        dt = _parse_iso((parts[0] or "").strip())
        if dt and dt >= cut:
            kept.append(line)

    try:
        NEWS_SEEN_TSV.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    except Exception:
        pass


def load_news_sources(path: Path) -> List[str]:
    if not path.exists():
        raise RuntimeError(f"news_sources.txt not found: {path}")
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _source_to_username_like(src: str) -> str:
    s = (src or "").strip()
    for pref in ("https://t.me/", "http://t.me/", "t.me/"):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    if s.startswith("@"):
        s = s[1:]
    return s.strip().strip("/")


def _post_url(username: str, msg_id: int) -> str:
    u = (username or "").strip().strip("/")
    return f"https://t.me/{u}/{int(msg_id)}"


def _build_prompt(posts: List[Dict[str, str]], limit: int) -> str:
    """
    Просим строго JSON:
      [{ "title": "...", "summary": "...", "url": "https://t.me/..." }, ...]
    """
    posts = posts[:2000]
    payload = [{"url": p["url"], "text": p["text"]} for p in posts]
    payload_json = json.dumps(payload, ensure_ascii=False)

    return (
        f"Ты — редактор новостного дайджеста.\n"
        f"Тебе дан список Telegram-постов за последние 12 часов (url + text).\n"
        f"Выбери максимум {limit} главных новостей.\n\n"
        f"Правила:\n"
        f"- Сгруппируй похожие посты в одно событие.\n"
        f"- Важность выше, если событие встречается в нескольких постах/каналах.\n"
        f"- Пиши на РУССКОМ.\n"
        f"- Не придумывай факты, опирайся только на тексты.\n"
        f"- Верни ТОЛЬКО JSON-массив объектов, без текста вокруг.\n"
        f"- Каждый объект: {{\"title\":\"...\",\"summary\":\"...\",\"url\":\"...\"}}\n"
        f"- title: до ~110 символов.\n"
        f"- summary: 1 строка.\n"
        f"- url: одна ссылка из входных url.\n\n"
        f"Входные посты (JSON):\n{payload_json}\n"
    )


def _call_openai(posts: List[Dict[str, str]], limit: int) -> List[dict]:
    """
    FIX: вызываем Responses API одинаково для GPT-4/4o/5 (как в chatgpt_dialog),
    и парсим JSON даже если модель добавила обёртку вокруг массива.
    """
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
    """
    Требования:
    - pinned игнорировать
    - посты без текста игнорировать
    - посты "только ссылка" игнорировать
    - forwarded включаем
    """
    cutoff = _now_utc() - timedelta(hours=max(1, int(hours)))
    out: List[Dict[str, str]] = []

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(f"Telethon session is NOT authorized: {SESSION_BASE}.session")

        for src in sources:
            try:
                entity = await client.get_entity(src)
            except Exception:
                continue

            username = getattr(entity, "username", None) or _source_to_username_like(src)
            if not username:
                continue

            try:
                async for msg in client.iter_messages(entity, offset_date=cutoff, reverse=True):
                    if bool(getattr(msg, "pinned", False)):
                        continue

                    text = (getattr(msg, "message", None) or "").strip()
                    if not text:
                        continue
                    if _is_text_only_links(text):
                        continue

                    msg_id = getattr(msg, "id", None)
                    if msg_id is None:
                        continue

                    tg_dt = getattr(msg, "date", None)
                    if tg_dt is None:
                        continue
                    if tg_dt.tzinfo is None:
                        tg_dt = tg_dt.replace(tzinfo=timezone.utc)
                    tg_dt = tg_dt.astimezone(timezone.utc)
                    if tg_dt < cutoff:
                        continue

                    out.append(
                        {
                            "url": _post_url(username, int(msg_id)),
                            "text": text,
                            "ts_utc": tg_dt.isoformat(),
                        }
                    )

            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception:
                continue

        return out
    finally:
        await client.disconnect()


def build_html_message(items: List[DigestItem], *, hours: int) -> str:
    if not items:
        return f"Новых значимых новостей за последние {hours} часов нет."

    lines: List[str] = [f"📰 Главные новости за последние {hours} часов", ""]
    for i, it in enumerate(items, start=1):
        title = html.escape((it.title or "").strip() or "Новость")
        summary = html.escape((it.summary or "").strip())
        url = (it.url or "").strip()

        lines.append(f'{i}. <a href="{url}">{title}</a>')
        if summary:
            lines.append(f"— {summary}")
        lines.append("")
    return "\n".join(lines).strip()


async def get_news_digest(*, news_sources_path: Path, hours: int = 12, limit: int = 10) -> List[DigestItem]:
    from ingest_runner import run_once
    run_once()

    """
    Главная функция для main.py:
    - память на RETENTION_DAYS
    - анти-повтор по событиям (title+summary)
    - back-compat: не повторяем также точные URL
    """
    _prune_seen(RETENTION_DAYS)
    seen_event_ids, seen_url_ids = _load_seen_sets()

    sources = load_news_sources(news_sources_path)
    posts = await fetch_posts_last_hours(sources, hours)
    if not posts:
        return []

    data: List[dict] = []
    for _ in range(1, MAX_TRIES + 1):
        try:
            data = _call_openai(posts, limit)
            break
        except Exception:
            time.sleep(SLEEP_BETWEEN_TRIES_SEC)

    out: List[DigestItem] = []
    for x in (data or []):
        title = str(x.get("title") or "").strip()
        summary = str(x.get("summary") or "").strip()
        url = str(x.get("url") or "").strip()

        if not url.startswith("https://t.me/"):
            continue

        event_id = _event_id_from_title_summary(title, summary)
        uh = _url_hash(url)

        if event_id in seen_event_ids:
            continue
        if uh in seen_url_ids:
            continue

        out.append(DigestItem(event_id=event_id, title=title, summary=summary, url=url))
        if len(out) >= max(0, int(limit)):
            break

    return out


def mark_digest_as_seen(items: List[DigestItem]) -> None:
    for it in items:
        _append_seen(it.event_id, it.url, it.title)
