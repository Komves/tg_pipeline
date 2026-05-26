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

    user = message.from_user
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip() or str(user.id)

    storage.register_group(
        group_id=int(message.chat.id),
        owner_user_id=int(user.id),
        group_title=group_title,
        last_seen_at=now_iso,
    )

    storage.register_group_member(
        group_id=int(message.chat.id),
        telegram_user_id=int(user.id),
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=full_name,
        last_seen_at=now_iso,
    )