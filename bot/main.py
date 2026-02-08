import time
from datetime import datetime, timezone

import ingest_runner
import nsfw_runner
import ranker


SLEEP_SECONDS = 900  # 15 min


def log(msg: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[main] {now} {msg}", flush=True)


def run_cycle():
    log("cycle start")

    # ingest
    try:
        log("ingest start")
        ingest_runner.run()
        log("ingest done")
    except Exception as e:
        log(f"ingest error: {e}")

    # nsfw scoring
    try:
        log("nsfw scoring start")
        scored = nsfw_runner.score_missing_b()
        log(f"nsfw scoring done: scored={scored}")
    except Exception as e:
        log(f"nsfw scoring error: {e}")

    # ranking
    try:
        log("ranking start")
        ranker.rank()
        log("ranking done")
    except Exception as e:
        log(f"ranking error: {e}")

    log("cycle end")


def main():
    log("worker started")

    while True:
        run_cycle()
        log(f"sleep {SLEEP_SECONDS}s")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
