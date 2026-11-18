"""Special-case Telegram /start before any dialog, memory, or LLM routing."""
from __future__ import annotations

import re


def is_bare_start(text: str) -> bool:
    """Only plain Telegram /start. Keep deep links like /start proxy intact."""
    return bool(re.fullmatch(r"/start(?:@[A-Za-z0-9_]+)?", (text or "").strip(), re.IGNORECASE))


START_GREETING = (
    "Веся на связи. Напиши, что нужно, обычными словами.\n\n"
    "Для подключения ChatGPT напиши: «Веся, подключи мне GPT».\n"
    "Список возможностей: «Веся, что ты умеешь?»"
)
