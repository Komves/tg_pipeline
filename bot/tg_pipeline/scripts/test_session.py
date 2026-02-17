from telethon import TelegramClient
import os, asyncio

API_ID = 36061375
API_HASH = "333af701e5d14db02d4b1040c09a699b"

ROOT = os.path.abspath(".")
SESSION = os.path.join(ROOT, "out", "sessions", "meme_session")

async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    auth = await client.is_user_authorized()
    print("AUTHORIZED =", auth)

    if auth:
        me = await client.get_me()
        print("USER ID =", me.id)
        print("USERNAME =", me.username)
        print("PHONE =", me.phone)

    await client.disconnect()

asyncio.run(main())
