import os
import asyncio
from telethon import TelegramClient

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]

DATA_DIR = os.environ.get("DATA_DIR", "/data")
SESSION = os.environ.get("TG_SESSION", "tg_session")

SESSION_PATH = f"{DATA_DIR}/{SESSION}"

async def ingest():
    print("Starting Telethon ingest...")
    print("Session:", SESSION_PATH)

    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

    await client.start()

    me = await client.get_me()
    print("Connected as:", me.username, me.id)

    # TEST: просто проверим, что можем читать диалоги
    dialogs = await client.get_dialogs(limit=5)

    print("Dialogs:")
    for d in dialogs:
        print("-", d.name)

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(ingest())
