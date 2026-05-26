from __future__ import annotations

from datetime import datetime


async def register_seen_group(storage, message, now_iso: str) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return

    if not message.from_user:
        return

    group_title = (message.chat.title or "").strip()
    if not group_title:
        return

    storage.register_group(
        group_id=int(message.chat.id),
        owner_user_id=int(message.from_user.id),
        group_title=group_title,
        last_seen_at=now_iso,
    )