import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError


# ========= paths / repo layout =========
# This file is inside: <repo_root>/bot/ingest_runner.py
# Render Root Directory is set to "bot", so cwd == <repo_root>/bot.
# Therefore we resolve repo root via __file__.
REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Telethon session base name (Telethon adds ".session")
# We use a base name inside /data, e.g. "/data/tg_session"
SESSION_BASE = os.getenv("TG_SESSION", str(DATA_DIR / "tg_session"))

# sources live outside bot/ at: <repo_root>/tg_pipeline/sources.txt
SOURCES_FILE = REPO_ROOT / "tg_pipeline" / "sources.txt"

# where to store downloaded media (Render disk)
RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ========= env / credentials =========
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]


# ========= helpers =========
def load_sources() -> list[str]:
    if not SOURCES_FILE.exists():
        raise RuntimeError(f"sources.txt not found: {SOURCES_FILE}")

    sources: list[str] = []
    with SOURCES_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            sources.append(s)
    return sources


def safe_channel_dir(name: str) -> Path:
    safe = name.strip().replace("@", "").replace("/", "_").replace("\\", "_")
    p = RAW_DIR / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def cutoff_utc(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def download_media(client: TelegramClient, message, channel_dir: Path) -> None:
    if not getattr(message, "media", None):
        return
    try:
        # Telethon will choose proper extension; we only pass base path
        target = channel_dir / f"{message.id}"
        await client.download_media(message.media, file=str(target))
    except Exception as e:
        print(f"[media] download error msg_id={getattr(message, 'id', '?')}: {e}")


# ========= core =========
async def ingest_hours(hours: int) -> None:
    sources = load_sources()
    cutoff = cutoff_utc(hours)

    print(f"[INGEST] hours={hours} sources={len(sources)}")
    print(f"[INGEST] sources_file={SOURCES_FILE}")
    print(f"[INGEST] data_dir={DATA_DIR} raw_dir={RAW_DIR}")
    print(f"[INGEST] session_base={SESSION_BASE}")

    async with TelegramClient(SESSION_BASE, API_ID, API_HASH) as client:
        for src in sources:
            channel_dir = safe_channel_dir(src)
            count = 0

            try:
                entity = await client.get_entity(src)

                # reverse=True yields messages from oldest->newest within offset window
                async for msg in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True,
                ):
                    if not getattr(msg, "media", None):
                        continue
                    await download_media(client, msg, channel_dir)
                    count += 1

            except FloodWaitError as e:
                print(f"[FloodWait] {src}: sleeping {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                print(f"[ERROR] {src}: {e}")

            print(f"[OK] {src}: downloaded={count}")


# ========= CLI (optional) =========
if __name__ == "__main__":
    import sys

    h = 24
    if len(sys.argv) > 1:
        h = int(sys.argv[1])

    asyncio.run(ingest_hours(h))
