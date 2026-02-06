import os
from telethon import TelegramClient

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
PHONE = os.getenv("TG_PHONE")

client = TelegramClient(
    "/data/tg_session",
    API_ID,
    API_HASH
)
