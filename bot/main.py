# bot/main.py

import os
import json
import asyncio
import hashlib
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import ingest_runner
import nsfw_runner
import ranker
import c_youtube_fetcher

try:
    import meme_ranker
except Exception:
    meme_ranker = None

try:
    import b_video_ranker
except Exception:
    b_video_ranker = None


BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR = DATA_DIR / "raw"

POSTED_TSV = DATA_DIR / "a_posted_master.tsv"
FEEDBACK_TSV = DATA_DIR / "feedback.tsv"
SENT_INDEX_JSON = DATA_DIR / "sent_index.json"
STATE_PATH = DATA_DIR / "daily_state.json"

C_POSTED_TSV = DATA_DIR / "c_posted_master.tsv"

A_MEMES_LIMIT = int(os.getenv("A_MEMES_LIMIT", "30"))
A_VIDEOS_LIMIT = int(os.getenv("A_VIDEOS_LIMIT", "20"))
B_VIDEOS_LIMIT = int(os.getenv("B_VIDEOS_LIMIT", "5"))

HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))

MSK = ZoneInfo("Europe/Moscow")
AUTO_DEADLINE_MSK = dtime(6, 0, 0)

_run_lock = asyncio.Lock()
router = Router()


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


def _resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID
    if ADMIN_USER_IDS:
        s = ADMIN_USER_IDS.replace(",", " ").split()
        if s:
            return s[0].strip()
    return ""


def _chat_user_id() -> int:
    cid = _resolve_chat_id()
    try:
        return int(cid)
    except Exception:
        return 0


def _ensure_posted_header() -> None:
    if not POSTED_TSV.exists():
        POSTED_TSV.write_text("timestamp\tuser_id\titem_id\tfeed\n", encoding="utf-8")


def _load_posted(user_id: int, feed: str) -> set[str]:
    if not POSTED_TSV.exists():
        return set()
    out = set()
    for line in POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        _ts, u, item, f = parts[0], parts[1], parts[2], parts[3]
        if str(u) == str(user_id) and f == feed:
            out.add(item)
    return out


def _mark_posted(user_id: int, item_id: str, feed: str) -> None:
    _ensure_posted_header()
    ts = datetime.now(timezone.utc).isoformat()
    with POSTED_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{user_id}\t{item_id}\t{feed}\n")


# ===== Category C storage =====

def _ensure_c_posted_header() -> None:
    if not C_POSTED_TSV.exists():
        C_POSTED_TSV.write_text("ts_utc\tvideo_id\turl\ttitle\n", encoding="utf-8")


def _load_c_posted_video_ids() -> set[str]:
    if not C_POSTED_TSV.exists():
        return set()
    out = set()
    for line in C_POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("ts_utc"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        out.add(parts[1])
    return out


def _mark_c_posted(video_id: str, url: str, title: str) -> None:
    _ensure_c_posted_header()
    ts = datetime.now(timezone.utc).isoformat()
    with C_POSTED_TSV.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{video_id}\t{url}\t{title}\n")


# ===== feedback =====

def _load_sent_index() -> Dict[str, Any]:
    if not SENT_INDEX_JSON.exists():
        return {}
    try:
        return json.loads(SENT_INDEX_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sent_index(d: Dict[str, Any]) -> None:
    SENT_INDEX_JSON.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _append_feedback(user_id: int, action: str, payload: Dict[str, Any]) -> None:
    if not FEEDBACK_TSV.exists():
        FEEDBACK_TSV.write_text(
            "ts_utc\tuser_id\taction\tfeed\tsid\tsrc\titem_id\tabs_path\n",
            encoding="utf-8",
        )
    ts = datetime.now(timezone.utc).isoformat()
    with FEEDBACK_TSV.open("a", encoding="utf-8") as f:
        f.write(
            f"{ts}\t{user_id}\t{action}\t{payload.get('feed')}\t{payload.get('sid')}\t"
            f"{payload.get('src')}\t{payload.get('item_id')}\t{payload.get('abs_path')}\n"
        )


def _sid(feed: str, item_id: str) -> str:
    return hashlib.sha1(f"{feed}:{item_id}".encode()).hexdigest()[:12]


def _kb_for_sid(sid: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="👍 Нравится", callback_data=f"fb:good:{sid}")
    kb.button(text="👎 Не нравится", callback_data=f"fb:bad:{sid}")
    kb.adjust(2)
    return kb.as_markup()


# ===== send =====

async def _send_one(bot: Bot, chat_id: str, it: Dict[str, Any]) -> bool:

    if it["feed"] == "c_youtube":

        sid = _sid("c_youtube", it["item_id"])

        text = f"🎸 {it['title']}\n{it['url']}"

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=_kb_for_sid(sid),
        )

        sent = _load_sent_index()
        sent[sid] = {
            "sid": sid,
            "feed": "c_youtube",
            "item_id": it["item_id"],
            "abs_path": "",
            "src": it["url"],
        }
        _save_sent_index(sent)

        return True

    return False


async def send_batch(bot: Bot, items: List[Dict[str, Any]]) -> int:

    chat_id = _resolve_chat_id()
    posted_c = _load_c_posted_video_ids()

    sent_total = 0

    for it in items:

        if it["feed"] == "c_youtube":

            vid = it["item_id"]

            if vid in posted_c:
                continue

            ok = await _send_one(bot, chat_id, it)

            if ok:
                _mark_c_posted(vid, it["url"], it["title"])
                sent_total += 1

    return sent_total


async def run_all(hours: int, *, reason: str):

    c_limit = 6 if hours >= 24 else 2 if hours >= 12 else 0

    posted = _load_c_posted_video_ids()

    c_items = c_youtube_fetcher.get_batch(
        limit=c_limit,
        posted_video_ids=posted,
    )

    bot = Bot(token=BOT_TOKEN)

    await send_batch(bot, c_items)

    await bot.session.close()


@router.message(Command("get12"))
async def cmd_get12(msg: Message):
    await msg.answer("Запуск C категории (2 ссылки)")
    asyncio.create_task(run_all(12, reason="manual"))


@router.callback_query()
async def on_feedback(cb: CallbackQuery):

    parts = cb.data.split(":")

    sid = parts[2]

    sent = _load_sent_index()

    payload = sent.get(sid)

    if payload:
        _append_feedback(cb.from_user.id, parts[1], payload)

    await cb.answer("OK")


async def scheduler_loop():

    while True:

        await run_all(24, reason="auto")

        await asyncio.sleep(86400)


async def main_async():

    bot = Bot(token=BOT_TOKEN)

    dp = Dispatcher()

    dp.include_router(router)

    await asyncio.gather(
        dp.start_polling(bot),
        scheduler_loop(),
    )


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
