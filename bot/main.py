import os
import time
import asyncio
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker

from aiogram import Bot


BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

# primary: explicit CHAT_ID
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

# fallback: first id from ADMIN_USER_IDS="357070178,...."
ADMIN_USER_IDS = (os.getenv("ADMIN_USER_IDS") or "").strip()

SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
INGEST_HOURS = int(os.getenv("INGEST_HOURS", "72"))
INGEST_TIMEOUT = int(os.getenv("INGEST_TIMEOUT", "300"))
RANK_N = int(os.getenv("RANK_N", "3"))


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[main {now}] {msg}", flush=True)


def _resolve_chat_id() -> str:
    if CHAT_ID:
        return CHAT_ID

    if ADMIN_USER_IDS:
        # allow "123", "123,456", "123 456"
        s = ADMIN_USER_IDS.replace(",", " ").split()
        if s:
            return s[0].strip()

    return ""


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

            # IMPORTANT: send file object, NOT path string,
            # because filenames may contain "https:" which Telegram treats as URL.
            with open(path, "rb") as f:
                try:
                    await bot.send_video(chat_id=chat_id, video=f, caption=str(it.src))
                except Exception:
                    f.seek(0)
                    await bot.send_document(chat_id=chat_id, document=f, caption=str(it.src))

            sent += 1

        except Exception as e:
            log(f"send error: {e}")

    await bot.session.close()
    log(f"send done sent={sent}")


def run_cycle():
    log("cycle start")

    asyncio.run(ingest())

    # nsfw scoring (quota-safe)
    try:
        nsfw_runner.score_missing_b()
    except Exception as e:
        log(f"nsfw scoring error: {e}")

    # ranking
    items = ranker.rank_top_n(
        user_id=0,
        category=ranker.CAT_A_VIDEO,
        n=RANK_N,
    )

    log(f"ranked {len(items)} items")

    if items:
        asyncio.run(send(items))

    log("cycle end")


def main():
    log("worker started")
    log(f"env: BOT_TOKEN={'set' if BOT_TOKEN else 'missing'} CHAT_ID={'set' if CHAT_ID else 'missing'} ADMIN_USER_IDS={'set' if ADMIN_USER_IDS else 'missing'}")

    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
