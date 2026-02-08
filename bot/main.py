import os
import time
import asyncio
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker

from aiogram import Bot


BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

SLEEP_SECONDS = 900
INGEST_HOURS = 72
INGEST_TIMEOUT = 300
RANK_N = 3


def log(msg):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[main {now}] {msg}", flush=True)


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

    if not CHAT_ID:
        log("CHAT_ID missing")
        return

    bot = Bot(token=BOT_TOKEN)

    for it in items:
        try:
            log(f"sending {it.abs_path}")
            await bot.send_video(
                chat_id=CHAT_ID,
                video=it.abs_path,
                caption=str(it.src),
            )
        except Exception as e:
            log(f"send error: {e}")

    await bot.session.close()


def run_cycle():
    log("cycle start")

    asyncio.run(ingest())

    nsfw_runner.score_missing_b()

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

    while True:
        run_cycle()
        log("sleep 900s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
