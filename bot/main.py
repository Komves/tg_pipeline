import os
import time
import asyncio
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker


SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))          # 15 min
INGEST_HOURS = int(os.getenv("INGEST_HOURS", "72"))             # сколько часов ingest
NSFW_HOURS = int(os.getenv("NSFW_HOURS", str(INGEST_HOURS)))    # окно для nsfw scoring
NSFW_LIMIT = int(os.getenv("NSFW_LIMIT", "50"))                 # совместимость со старым runner.limit

# ранжирование (пока просто логируем топ, отправка может быть отдельным модулем)
RANK_USER_ID = int(os.getenv("RANK_USER_ID", "0"))
RANK_N = int(os.getenv("RANK_N", "10"))


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


def run_cycle() -> None:
    log("cycle start")

    # 1) ingest
    try:
        log(f"ingest start (hours={INGEST_HOURS})")
        asyncio.run(ingest_runner.ingest_hours(INGEST_HOURS))
        log("ingest done")
    except Exception as e:
        log(f"ingest error: {repr(e)}")

    # 2) nsfw scoring
    try:
        log(f"nsfw scoring start (hours={NSFW_HOURS}, limit={NSFW_LIMIT})")
        scored = nsfw_runner.score_missing_b(hours=NSFW_HOURS, limit=NSFW_LIMIT)
        log(f"nsfw scoring done: scored={scored}")
    except Exception as e:
        log(f"nsfw scoring error: {repr(e)}")

    # 3) ranking (логируем топ; отправка может быть отдельным шагом)
    try:
        log(f"ranking start (user_id={RANK_USER_ID}, n={RANK_N})")
        items = ranker.rank_top_n(RANK_USER_ID, ranker.CAT_A_VIDEO, RANK_N, feed="feed_a_video")
        log(f"ranking done: items={len(items)}")
        for i, it in enumerate(items[:RANK_N], 1):
            log(f"rank#{i} score={it.score:.4f} src={it.src} path={it.abs_path}")
    except Exception as e:
        log(f"ranking error: {repr(e)}")

    log("cycle end")


def main() -> None:
    log("worker started")
    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
