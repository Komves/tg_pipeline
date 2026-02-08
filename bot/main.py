import os
import time
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import ingest_runner
import ranker

from aiogram import Bot
from aiogram.types import FSInputFile


# -------------------------
# ENV
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()

SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))          # 15 min
INGEST_HOURS = int(os.getenv("INGEST_HOURS", "72"))
INGEST_TIMEOUT = int(os.getenv("INGEST_TIMEOUT", "300"))

RANK_N = int(os.getenv("RANK_N", "3"))

# B scoring gate (RapidAPI)
ENABLE_B_SCORING = (os.getenv("ENABLE_B_SCORING", "0").strip() == "1")
B_SCORING_MAX_PER_DAY = int(os.getenv("B_SCORING_MAX_PER_DAY", "1"))  # run at most N times/day (обычно 1)

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
B_STATE_PATH = DATA_DIR / "b_scoring_state.json"


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[main {now}] {msg}", flush=True)


def _resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID
    if ADMIN_USER_IDS:
        s = ADMIN_USER_IDS.replace(",", " ").split()
        if s:
            return s[0].strip()
    return ""


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_b_state() -> dict:
    if not B_STATE_PATH.exists():
        return {}
    try:
        return json.loads(B_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_b_state(d: dict) -> None:
    try:
        B_STATE_PATH.write_text(json.dumps(d), encoding="utf-8")
    except Exception as e:
        log(f"b_state write error: {e}")


def _b_scoring_allowed_now() -> bool:
    """
    Allow B scoring at most B_SCORING_MAX_PER_DAY runs per UTC day.
    This prevents quota burn even if loop runs every 15 minutes.
    """
    if not ENABLE_B_SCORING:
        return False

    state = _load_b_state()
    day = _utc_day()
    runs_today = int(state.get(day, 0))
    return runs_today < B_SCORING_MAX_PER_DAY


def _mark_b_scoring_run() -> None:
    state = _load_b_state()
    day = _utc_day()
    state[day] = int(state.get(day, 0)) + 1
    _save_b_state(state)


async def ingest():
    try:
        await asyncio.wait_for(
            ingest_runner.ingest_hours(INGEST_HOURS),
            timeout=INGEST_TIMEOUT,
        )
    except Exception as e:
        log(f"ingest stop: {e}")


async def send(items):
    if not BOT_TOKEN:
        log("BOT_TOKEN missing")
        return

    chat_id = _resolve_chat_id()
    if not chat_id:
        log("CHAT_ID missing (and ADMIN_USER_IDS empty) -> cannot send")
        return

    bot = Bot(token=BOT_TOKEN)

    sent = 0
    for it in items:
        path = it.abs_path
        try:
            log(f"sending {path} -> chat_id={chat_id}")

            file = FSInputFile(path)
            try:
                await bot.send_video(chat_id=chat_id, video=file, caption=str(it.src))
            except Exception:
                file = FSInputFile(path)
                await bot.send_document(chat_id=chat_id, document=file, caption=str(it.src))

            sent += 1
        except Exception as e:
            log(f"send error: {e}")

    await bot.session.close()
    log(f"send done sent={sent}")


def run_cycle():
    log("cycle start")

    # 1) ingest (A pipeline doesn't depend on RapidAPI)
    asyncio.run(ingest())

    # 2) B scoring (RapidAPI) — ONLY if enabled, and ONLY once per day
    if _b_scoring_allowed_now():
        try:
            import nsfw_runner  # local import so A doesn't depend on it
            log("B scoring start (quota-protected, daily)")
            nsfw_runner.score_missing_b()
            _mark_b_scoring_run()
            log("B scoring done")
        except Exception as e:
            # IMPORTANT: do not block A
            log(f"B scoring stop: {e}")
            _mark_b_scoring_run()  # even on quota/429 we consider today's run consumed
    else:
        if not ENABLE_B_SCORING:
            log("B scoring skipped (ENABLE_B_SCORING=0)")
        else:
            log("B scoring skipped (daily limit reached)")

    # 3) A ranking + send (no RapidAPI)
    items = ranker.rank_top_n(
        user_id=0,
        category=ranker.CAT_A_VIDEO,
        n=RANK_N,
    )
    log(f"ranked {len(items)} A items")

    if items:
        asyncio.run(send(items))

    log("cycle end")


def main():
    log("worker started")
    log(f"env: ENABLE_B_SCORING={int(ENABLE_B_SCORING)} B_SCORING_MAX_PER_DAY={B_SCORING_MAX_PER_DAY}")

    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
