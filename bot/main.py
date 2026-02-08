import os
import time
import asyncio
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker


SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
INGEST_HOURS = int(os.getenv("INGEST_HOURS", "72"))
INGEST_TIMEOUT = int(os.getenv("INGEST_TIMEOUT", "300"))  # 5 min timeout

NSFW_HOURS = int(os.getenv("NSFW_HOURS", str(INGEST_HOURS)))
NSFW_LIMIT = int(os.getenv("NSFW_LIMIT", "50"))

RANK_USER_ID = int(os.getenv("RANK_USER_ID", "0"))
RANK_N = int(os.getenv("RANK_N", "10"))


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


async def ingest_with_timeout(hours: int, timeout: int):
    try:
        await asyncio.wait_for(
            ingest_runner.ingest_hours(hours),
            timeout=timeout,
        )
        return True
    except asyncio.TimeoutError:
        log(f"ingest TIMEOUT after {timeout}s")
        return False
    except Exception as e:
        log(f"ingest error: {repr(e)}")
        return False


def run_cycle():
    log("cycle start")

    # ingest with timeout
    try:
        log(f"ingest start (hours={INGEST_HOURS}, timeout={INGEST_TIMEOUT}s)")
        asyncio.run(ingest_with_timeout(INGEST_HOURS, INGEST_TIMEOUT))
        log("ingest finished")
    except Exception as e:
        log(f"ingest fatal error: {repr(e)}")

    # nsfw
    try:
        log("nsfw scoring start")
        scored = nsfw_runner.score_missing_b(hours=NSFW_HOURS, limit=NSFW_LIMIT)
        log(f"nsfw scoring done: scored={scored}")
    except Exception as e:
        log(f"nsfw scoring error: {repr(e)}")

    # ranking
    try:
        log("ranking start")
        items = ranker.rank_top_n(RANK_USER_ID, ranker.CAT_A_VIDEO, RANK_N)
        log(f"ranking done: items={len(items)}")
    except Exception as e:
        log(f"ranking error: {repr(e)}")

    log("cycle end")


def main():
    log("worker started")

    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
