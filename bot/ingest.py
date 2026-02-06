import asyncio
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from ingest_runner import (
    load_sources,
    safe_channel_dir,
    download_media,
    SESSION_BASE,
    API_ID,
    API_HASH,
)


async def ingest_hours(hours: int) -> None:
    sources = load_sources()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    client = TelegramClient(SESSION_BASE, API_ID, API_HASH)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session is not authorized on Render. "
                "You must have a valid session file on disk: "
                f"{SESSION_BASE}.session"
            )

        for src in sources:
            channel_dir = safe_channel_dir(src)
            downloaded = 0

            try:
                entity = await client.get_entity(src)

                async for msg in client.iter_messages(
                    entity,
                    offset_date=cutoff,
                    reverse=True,
                ):
                    if not getattr(msg, "media", None):
                        continue
                    await download_media(client, msg, channel_dir)
                    downloaded += 1

            except FloodWaitError as e:
                print(f"[FloodWait] {src}: sleep {e.seconds}s")
                await asyncio.sleep(e.seconds)

            except Exception as e:
                print(f"[ERROR] {src}: {e}")

            print(f"[OK] {src}: downloaded={downloaded}")

    finally:
        await client.disconnect()
