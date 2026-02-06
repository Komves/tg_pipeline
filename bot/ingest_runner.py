import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

# ========= CONFIG =========

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SESSION_PATH = DATA_DIR / "tg_session"

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]

SOURCES_FILE = Path("tg_pipeline") / "sources.txt"

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

MOSCOW_TZ = timezone(timedelta(hours=3))


# ========= UTIL =========

def load_sources():
    if not SOURCES_FILE.exists():
        raise RuntimeError(f"sources.txt not found at {SOURCES_FILE}")

    sources = []
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sources.append(line)

    return sources


def get_cutoff(hours: int):
    now = datetime.now(timezone.utc)
    return now - timedelta(hours=hours)


def get_channel_dir(channel: str):
    safe = channel.replace("@", "").replace("/", "_")
    path = RAW_DIR / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


async def save_message_media(client, message, channel_dir):
    if not message.media:
        return

    try:
        filename = f"{message.id}"
        await client.download_media(
            message.media,
            file=channel_dir / filename
        )
    except Exception as e:
        print(f"media download error: {e}")


# ========= CORE INGEST =========

async def ingest_hours(hours: int):

    cutoff = get_cutoff(hours)
    sources = load_sources()

    print(f"[INGEST] hours={hours}, sources={len(sources)}")

    async with TelegramClient(
        str(SESSION_PATH),
        API_ID,
        API_HASH
    ) as client:

        for source in sources:

            try:
                entity = await client.get_entity(source)
            except Exception as e:
                print(f"[ERROR] entity {source}: {e}")
                continue

            channel_dir = get_channel_dir(source)

            count = 0

            try:
                async for message in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True
                ):

                    if not message.media:
                        continue

                    await save_message_media(client, message, channel_dir)

                    count += 1

            except FloodWaitError as e:
                print(f"[FloodWait] sleeping {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                print(f"[ERROR] ingest {source}: {e}")

            print(f"[OK] {source}: {count} items")


# ========= PUBLIC FUNCTIONS =========

async def ingest_last_24h():
    await ingest_hours(24)


async def ingest_last_12h():
    await ingest_hours(12)


# ========= CLI MODE =========

if __name__ == "__main__":

    import sys

    hours = 24

    if len(sys.argv) > 1:
        hours = int(sys.argv[1])

    asyncio.run(ingest_hours(hours))

