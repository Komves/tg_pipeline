import os
import json
import asyncio
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import ingest_runner
import nsfw_runner
import ranker

# optional: if you already use them in your project, they will be used
try:
    import meme_ranker
except Exception:
    meme_ranker = None

try:
    import b_video_ranker
except Exception:
    b_video_ranker = None

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile


# -------------------------
# ENV (no new flags)
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# heartbeat only (logs + scheduler checks)
HEARTBEAT_SEC = 300  # 5 minutes, not a "work frequency"

# Auto daily run window: 00:00–06:00 Moscow time
MSK = ZoneInfo("Europe/Moscow")
AUTO_DEADLINE_MSK = dtime(6, 0, 0)  # 06:00 MSK

STATE_PATH = DATA_DIR / "daily_state.json"

# how many items to send per category in one run (keep simple defaults)
SEND_A_VIDEOS = 3
SEND_MEMES = 3
SEND_B_VIDEOS = 3

# Prevent concurrent runs
_run_lock = asyncio.Lock()


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


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"state write error: {e}")


def _today_msk_str() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def _is_in_auto_window_msk() -> bool:
    now_msk = datetime.now(MSK).time()
    return now_msk <= AUTO_DEADLINE_MSK  # from 00:00 until 06:00


def _auto_already_ran_today() -> bool:
    st = _load_state()
    return st.get("last_auto_msk_day") == _today_msk_str()


def _mark_auto_ran_today() -> None:
    st = _load_state()
    st["last_auto_msk_day"] = _today_msk_str()
    st["last_auto_ts_utc"] = datetime.now(timezone.utc).isoformat()
    _save_state(st)


async def _send_file(bot: Bot, chat_id: str, path: str, caption: str = "") -> None:
    p = Path(path)
    if not p.exists():
        log(f"send skip missing file: {path}")
        return

    file = FSInputFile(str(p))
    ext = p.suffix.lower()

    # keep simple: video/photo/document
    try:
        if ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
            await bot.send_video(chat_id=chat_id, video=file, caption=caption or None)
        elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
            await bot.send_photo(chat_id=chat_id, photo=file, caption=caption or None)
        else:
            await bot.send_document(chat_id=chat_id, document=file, caption=caption or None)
    except Exception as e:
        log(f"send error: {e}")


async def run_all(hours: int, *, reason: str) -> None:
    """
    One unified run: ingest -> (optional) nsfw scoring (safe) -> rank -> send
    hours controls the ingest window (12 or 24).
    """
    async with _run_lock:
        log(f"RUN start reason={reason} hours={hours}")

        # 1) ingest
        try:
            log(f"ingest start hours={hours}")
            await ingest_runner.ingest_hours(hours)
            log("ingest done")
        except Exception as e:
            log(f"ingest error: {e}")

        # 2) nsfw scoring (this is for B; it is quota-safe and should never block)
        try:
            log("nsfw scoring start")
            nsfw_runner.score_missing_b(hours=hours)
            log("nsfw scoring done")
        except Exception as e:
            # 429 etc should not kill the run
            log(f"nsfw scoring stop: {e}")

        # 3) rank + send (A videos always)
        chat_id = _resolve_chat_id()
        if not chat_id:
            log("CHAT_ID missing -> cannot send")
            log("RUN end (no chat_id)")
            return

        bot = Bot(token=BOT_TOKEN)

        # A videos
        try:
            items = ranker.rank_top_n(user_id=0, category=ranker.CAT_A_VIDEO, n=SEND_A_VIDEOS)
            log(f"A videos ranked={len(items)}")
            for it in items:
                await _send_file(bot, chat_id, it.abs_path, caption=str(it.src))
        except Exception as e:
            log(f"A videos error: {e}")

        # Memes (if your meme_ranker is available and returns paths/objects)
        if meme_ranker is not None:
            try:
                cand = None
                if hasattr(meme_ranker, "rank_memes"):
                    cand = meme_ranker.rank_memes()
                elif hasattr(meme_ranker, "rank"):
                    cand = meme_ranker.rank()

                if cand:
                    sent = 0
                    for x in cand:
                        if sent >= SEND_MEMES:
                            break
                        path = getattr(x, "abs_path", None) or getattr(x, "path", None) or (x if isinstance(x, str) else None)
                        if not path:
                            continue
                        await _send_file(bot, chat_id, str(path), caption="meme")
                        sent += 1
                    log(f"memes sent={sent}")
            except Exception as e:
                log(f"memes error: {e}")

        # B videos (if your b_video_ranker is available)
        if b_video_ranker is not None:
            try:
                cand = None
                if hasattr(b_video_ranker, "rank_b_videos"):
                    cand = b_video_ranker.rank_b_videos()
                elif hasattr(b_video_ranker, "rank"):
                    cand = b_video_ranker.rank()

                if cand:
                    sent = 0
                    for x in cand:
                        if sent >= SEND_B_VIDEOS:
                            break
                        path = getattr(x, "abs_path", None) or getattr(x, "path", None) or (x if isinstance(x, str) else None)
                        if not path:
                            continue
                        await _send_file(bot, chat_id, str(path), caption="B video")
                        sent += 1
                    log(f"B videos sent={sent}")
            except Exception as e:
                log(f"B videos error: {e}")

        await bot.session.close()
        log("RUN end")


# -------------------------
# Bot commands
# -------------------------
router = Router()

@router.message(Command("get12"))
async def cmd_get12(msg: Message):
    await msg.answer("Ок, запускаю прогон за 12 часов.")
    asyncio.create_task(run_all(12, reason="manual_get12"))

@router.message(Command("get24"))
async def cmd_get24(msg: Message):
    await msg.answer("Ок, запускаю прогон за 24 часа.")
    asyncio.create_task(run_all(24, reason="manual_get24"))


async def scheduler_loop():
    """
    Heartbeat loop: does NOT run work every heartbeat.
    It only checks time and triggers auto 24h run once per MSK day in 00:00–06:00 window.
    """
    log("scheduler loop started")
    while True:
        try:
            if _is_in_auto_window_msk() and not _auto_already_ran_today():
                log("auto window hit -> starting auto 24h run")
                await run_all(24, reason="auto_daily_24h")
                _mark_auto_ran_today()
            else:
                # heartbeat log, so you see it is alive
                log("heartbeat")
        except Exception as e:
            log(f"scheduler error: {e}")

        await asyncio.sleep(HEARTBEAT_SEC)


async def main_async():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    log("worker started")
    log("commands: /get12 (manual 12h), /get24 (manual 24h)")
    log("auto: once per day in MSK 00:00–06:00 window")

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
