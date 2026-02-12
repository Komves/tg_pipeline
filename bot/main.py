# bot/main.py
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient, events

import chatgpt_dialog
import news_digest


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
NEWS_SOURCES = Path("news_sources.txt")

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = str(DATA_DIR / "tg_session")


client = TelegramClient(SESSION, API_ID, API_HASH)


# =========================
# NEWS
# =========================
async def run_news(event):
    items = await news_digest.get_news_digest(
        news_sources_path=NEWS_SOURCES,
        hours=12,
        limit=10,
    )

    text = news_digest.build_html_message(items, hours=12)
    await event.respond(text, parse_mode="html")

    news_digest.mark_digest_as_seen(items)


# =========================
# CONTENT STUB
# =========================
async def run_content(event):
    await event.respond("Контент пайплайн запущен.")


# =========================
# MAIN HANDLER
# =========================
@client.on(events.NewMessage(incoming=True))
async def vesya_handler(event):
    if not event.message:
        return

    text = (event.message.message or "").strip()
    if not text:
        return

    chat_id = event.chat_id or 0
    user_id = event.sender_id or 0

    decision = chatgpt_dialog.decide(chat_id, user_id, text)

    intent = (decision.intent or "chat").strip().lower()

    # FIX: жёстко контролируем ack для news/content
    if intent == "news":
        await event.respond("Собираю новости.")
        await run_news(event)
        return

    if intent == "content":
        await event.respond("Собираю контент.")
        await run_content(event)
        return

    if intent == "end":
        await event.respond("Принято.")
        return

    # обычный чат
    reply = (decision.reply or "").strip()
    if reply:
        await event.respond(reply)


# =========================
# START
# =========================
async def main():
    await client.start()
    print("Vesya running.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
