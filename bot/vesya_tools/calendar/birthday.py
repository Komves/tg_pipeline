from __future__ import annotations

import html


def build_birthday_message(
    person_name: str,
    telegram_user_id: int | None = None,
    username: str | None = None,
) -> str:
    name = (person_name or "").strip() or "именинник"

    if username:
        mention = f"@{username.lstrip('@')}"
    elif telegram_user_id:
        mention = f'<a href="tg://user?id={int(telegram_user_id)}">{html.escape(name)}</a>'
    else:
        mention = html.escape(name)

    return (
        f"🎂 Сегодня у {html.escape(name)} день рождения.\n\n"
        f"{mention}, с днюхой 😄\n"
        f"Пусть мотор не троит, рыба клюет, а жизнь не требует капремонта."
    )