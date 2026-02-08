import os
import time
import asyncio
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker

from telethon import TelegramClient


API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
BOT_TOKEN = os.environ["TG_BOT_TOKEN"]

SESSION = "/data/bot_session"


SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
INGEST_HOURS = int(os.getenv("INGEST_HOURS", "72"))
INGEST_TIMEOUT = int(os.getenv("INGEST_TIMEOUT", "300"))

RANK_USER_ID = int(os.getenv("RANK_USER_ID", "0"))
RANK_N = int(os.getenv("RANK_N", "3"))


def log(msg):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[main {now}] {msg}", flush=True)


async def send_items(items):
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)

    for item in items:
        try:
            log(f"sending {item.abs_path}")
            await client.send_file(
                entity="me",
                file=item.abs_path,
                caption=f"{item.src}",
            )
        except Exception as e:
            log(f"send error: {e}")

    await client.disconnect()


async def ingest_with_timeout(hours, timeout):
    try:
        await asyncio.wait_for(
            ingest_runner.ingest_hours(hours),
            timeout=timeout,
        )
    except Exception as e:
        log(f"ingest stop: {e}")


def run_cycle():
    log("cycle start")

    asyncio.run(ingest_with_timeout(INGEST_HOURS, INGEST_TIMEOUT))

    nsfw_runner.score_missing_b()

    items = ranker.rank_top_n(
        RANK_USER_ID,
        ranker.CAT_A_VIDEO,
        RANK_N,
    )

    log(f"ranked items={len(items)}")

    if items:
        asyncio.run(send_items(items))

    log("cycle end")


def main():
    log("worker started")

    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
