from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Tuple, Any

from .core.models import ResearchTask
from .core.profile_registry import detect_profile, profiles_help, PROFILES
from .core.task_runner import run_task


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


def is_analytics_active(chat_id: int, user_id: int) -> bool:
    s = _SESSIONS.get(_key(chat_id, user_id))
    return bool(s and s.get("active"))


def _looks_like_followup(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(re.search(
        r"\b("
        r"подробнее|подробней|по источникам|что входит|расшифруй|"
        r"почему так|откуда цифры|что учтено|что не учтено|"
        r"добавь|учти|измени|пересчитай|сануз|ламинат|плитк|"
        r"двер|электрик|сантехник|план|планировку"
        r")\b",
        t,
        flags=re.I,
    ))


def _renovation_followup_reply(task: ResearchTask, text: str) -> str:
    from analytics_agent.profiles.renovation.parser import (
        parse_renovation_task,
        merge_renovation_params,
    )
    from analytics_agent.profiles.renovation.report import (
        build_renovation_report,
        build_renovation_followup,
    )

    t = (text or "").strip().lower()

    if re.search(r"\b(подробнее|подробней|по источникам|откуда цифры|почему так)\b", t):
        return build_renovation_followup(task.task_id, task.params, text)

    if re.search(r"\b(добавь|учти|измени|пересчитай|сануз|ламинат|плитк|двер|электрик|сантехник)\b", t):
        upd = parse_renovation_task(text)
        task.params = merge_renovation_params(task.params or {}, upd)
        task.status = "updated"
        _save_task(task)
        return build_renovation_report(task.task_id, task.params)

    return build_renovation_followup(task.task_id, task.params, text)


async def handle_analytics_photo(message, img_bytes: bytes, answer_long) -> bool:
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0
    k = _key(chat_id, user_id)

    session = _SESSIONS.get(k)
    if not session or not session.get("active"):
        return False

    if session.get("profile") != "renovation":
        return False

    task = session.get("last_task")
    if not isinstance(task, ResearchTask):
        from analytics_agent.profiles.renovation.parser import parse_renovation_task

        task = ResearchTask.create(
            profile="renovation",
            user_text="План квартиры",
            params=parse_renovation_task(""),
        )
        task.status = "created_from_layout"
        session["last_task"] = task

    from analytics_agent.profiles.renovation.layout_extractor import extract_layout_from_image
    from analytics_agent.profiles.renovation.report import build_renovation_report

    await message.answer("План получила. Извлекаю помещения и метражи.")

    layout = extract_layout_from_image(img_bytes)
    task.params["layout"] = layout

    if layout.get("total_area_m2") and not task.params.get("area_m2"):
        task.params["area_m2"] = float(layout["total_area_m2"])

    missing = []
    if task.params.get("area_m2") is None:
        missing.append("area_m2")
    if not task.params.get("city"):
        missing.append("city")
    if not task.params.get("repair_class"):
        missing.append("repair_class")
    if not task.params.get("property_type"):
        missing.append("property_type")
    task.params["missing"] = missing

    task.status = "layout_added"
    _save_task(task)

    session["mode"] = "WAIT_REQUIREMENTS"

    summary = [
        "План обработан.",
    ]

    if layout.get("total_area_m2"):
        summary.append(f"Площадь: {layout.get('total_area_m2')} м²")

    rooms = layout.get("rooms") or []
    if rooms:
        summary.append(f"Помещений распознано: {len(rooms)}")

    if layout.get("bathrooms") is not None:
        summary.append(f"Санузлов: {layout.get('bathrooms')}")

    summary.append("")
    summary.append(
        "Теперь напиши:\n"
        "• город\n"
        "• класс ремонта\n"
        "• нужны материалы или материалы+работы"
    )

    await answer_long(message, "\n".join(summary))
    return True

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

    if (
        "включи аналитика" in t
        or "включи режим аналитика" in t
        or "режим аналитика" in t
        or t == "аналитик"
    ):
        _SESSIONS[k] = {
            "active": True,
            "profile": None,
            "mode": "WAIT_PROFILE",
            "last_task": None,
        }
        await message.answer("Режим аналитика включен.\n\n" + profiles_help())
        return True

    session = _SESSIONS.get(k)
    if not session or not session.get("active"):
        return False

    profile = session.get("profile")

    detected = detect_profile(raw)

    if detected and not profile:
        session["profile"] = detected

        if detected == "renovation":
            session["mode"] = "WAIT_LAYOUT"

            await message.answer(
                f"Профиль выбран: {PROFILES[detected]['title']}.\n\n"
                "Теперь загрузи план квартиры.\n"
                "Я извлеку помещения, площадь и структуру."
            )
            return True

        await message.answer(
            f"Профиль выбран: {PROFILES[detected]['title']}."
        )
        return True

    if not profile:
        await message.answer(profiles_help())
        return True

    last_task = session.get("last_task")

    if isinstance(last_task, ResearchTask) and _looks_like_followup(raw):
        if last_task.profile == "renovation":
            reply = _renovation_followup_reply(last_task, raw)
            await answer_long(message, reply)
            return True

    existing = session.get("last_task")

    if (
        session.get("profile") == "renovation"
        and session.get("mode") == "WAIT_REQUIREMENTS"
        and isinstance(existing, ResearchTask)
    ):
        from analytics_agent.profiles.renovation.parser import (
            parse_renovation_task,
            merge_renovation_params,
        )
        from analytics_agent.profiles.renovation.report import (
            build_renovation_report,
        )

        upd = parse_renovation_task(raw)

        existing.params = merge_renovation_params(
            existing.params or {},
            upd,
        )

        existing.status = "requirements_added"

        session["mode"] = "READY"

        _save_task(existing)

        result = build_renovation_report(
            existing.task_id,
            existing.params,
        )

        await answer_long(message, result)
        return True

    task = ResearchTask.create(
        profile=profile,
        user_text=raw,
    )

    result = run_task(task)

    task.status = "done"
    session["last_task"] = task
    _save_task(task)

    await answer_long(message, result)
    return True