import os, asyncio
from telethon import TelegramClient

API_ID = 36061375
API_HASH = "333af701e5d14db02d4b1040c09a699b"

ROOT = os.path.abspath(".")
SESSION = os.path.join(ROOT, "data", "tg", "session", "tg_ingest")  # без .session

async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()
    print("SESSION:", SESSION + ".session")
    print("AUTHORIZED =", await client.is_user_authorized())
    if await client.is_user_authorized():
        me = await client.get_me()
        print("ME:", me.id, me.username, me.phone)
    await client.disconnect()

asyncio.run(main())
