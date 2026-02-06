from telethon import TelegramClient
import asyncio

API_ID = 36061375
API_HASH = "333af701e5d14db02d4b1040c09a699b"

async def main():
    client = TelegramClient("test_session", API_ID, API_HASH)
    await client.connect()
    print("CONNECTED OK")
    await client.disconnect()

asyncio.run(main())
