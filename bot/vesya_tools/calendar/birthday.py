from __future__ import annotations

import asyncio
import html
import os
import random
import re


def _display_name(person_name: str) -> str:
    name = (person_name or "").strip()
    return "именинник" if not name or re.fullmatch(r"id=\d+", name, flags=re.I) else name


def build_birthday_message(
    person_name: str,
    telegram_user_id: int | None = None,
    username: str | None = None,
    wish: str | None = None,
) -> str:
    """HTML caption for both birthday video and text-only fallback."""
    name = _display_name(person_name)
    escaped = html.escape(name)
    # Keep the friendly name visible even when a username is available.
    if telegram_user_id:
        mention = f'<a href="tg://user?id={int(telegram_user_id)}">{escaped}</a>'
    elif username:
        mention = f'<a href="https://t.me/{html.escape(username.lstrip("@"), quote=True)}">{escaped}</a>'
    else:
        mention = escaped

    greeting = (wish or "").strip().strip('"«» ')
    greeting = re.sub(r"(?is)\s*красоты тебе[!.\s😎]*$", "", greeting).strip()
    if not greeting:
        greeting = "Пусть приятных сюрпризов будет больше, чем поводов закатывать глаза."
    return f"🎂 {mention}, с днюхой!\n\n{html.escape(greeting)}\n\nКрасоты тебе! 😎"


def _generate_wish_sync(name: str, group_title: str) -> str:
    """Use the same model/persona family as Vesya's regular chat, without chat history."""
    from openai import OpenAI
    import persona

    model = os.getenv("V_BIRTHDAY_MODEL") or os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")
    instruction = (
        "Напиши ровно 1–2 коротких предложения с оригинальным поздравительным "
        "пожеланием для дня рождения в групповой беседе Telegram. "
        "Ты Веся: живая, остроумная, слегка дерзкая, но доброжелательная. "
        "Уместна лёгкая ирония. Не повторяй стандартные тосты, не выдумывай "
        "факты о человеке, не упоминай возраст, пол, здоровье или личные обстоятельства. "
        "Не добавляй имя, вступление, приветствие, хэштеги или фразу 'Красоты тебе'. "
        "Пиши только само пожелание, без кавычек. Максимум 180 символов."
    )
    context = f"Имя именинника: {name}. Группа: {group_title}."
    result = OpenAI().responses.create(
        model=model,
        input=[
            {"role": "system", "content": getattr(persona, "_SYSTEM_PROMPT", "") + "\n" + instruction},
            {"role": "user", "content": context},
        ],
    )
    reply = (getattr(result, "output_text", "") or "").strip().strip('"«» ')
    reply = re.sub(r"\s+", " ", reply)
    if not reply:
        raise ValueError("birthday model returned empty greeting")
    return reply[:220].rsplit(" ", 1)[0] if len(reply) > 220 else reply


async def generate_birthday_message(
    person_name: str,
    group_title: str,
    telegram_user_id: int | None = None,
    username: str | None = None,
) -> str:
    """Return safe HTML, with a local fallback if the model is unavailable."""
    name = _display_name(person_name)
    wish = None
    if os.getenv("OPENAI_API_KEY"):
        try:
            wish = await asyncio.to_thread(_generate_wish_sync, name, group_title)
        except Exception as exc:
            print(f"[birthday] generation failed: {type(exc).__name__}: {exc}", flush=True)
    if not wish:
        wish = random.choice((
            "Пусть хороших поводов улыбнуться будет неприлично много!",
            "Желаю ярких приключений и чтобы удача сама стучалась в дверь.",
            "Пусть всё задуманное удаётся, а скука обходит стороной.",
            "Пусть каждый новый день приносит приятные сюрпризы — без подвохов!",
        ))
    return build_birthday_message(name, telegram_user_id, username, wish)
