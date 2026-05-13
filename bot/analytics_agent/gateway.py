from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple, Any

from .core.models import ResearchTask
from .core.profile_registry import detect_profile, profiles_help, PROFILES
from .core.report_builder import build_mock_report


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
TASK_DIR = DATA_DIR / "analytics_tasks"
TASK_DIR.mkdir(parents=True, exist_ok=True)

_SESSIONS: Dict[Tuple[int, int], Dict[str, Any]] = {}


def _key(chat_id: int, user_id: int) -> Tuple[int, int]:
    return int(chat_id), int(user_id)


def _save_task(task: ResearchTask) -> None:
    path = TASK_DIR / f"{task.task_id}.json"
    path.write_text(
        json.dumps(task.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def handle_analytics_message(message, text: str, answer_long) -> bool:
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0
    k = _key(chat_id, user_id)

    raw = (text or "").strip()
    t = raw.lower()

    if "выключи аналитика" in t or "отключи аналитика" in t:
        _SESSIONS.pop(k, None)
        await message.answer("Режим аналитика выключен.")
        return True

    if "включи аналитика" in t or "аналитик" == t:
        _SESSIONS[k] = {"active": True, "profile": None}
        await message.answer("Режим аналитика включен.\n\n" + profiles_help())
        return True

    session = _SESSIONS.get(k)
    if not session or not session.get("active"):
        return False

    profile = session.get("profile")

    detected = detect_profile(raw)
    if detected:
        session["profile"] = detected
        await message.answer(
            f"Профиль выбран: {PROFILES[detected]['title']}.\n"
            f"Теперь напиши аналитическую задачу."
        )
        return True

    if not profile:
        await message.answer(profiles_help())
        return True

    task = ResearchTask.create(
        profile=profile,
        user_text=raw,
        params={},
    )
    task.status = "created"
    _save_task(task)

    report = build_mock_report(task)
    await answer_long(message, report)
    return True