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

async def handle_analytics_callback(cb, answer_long) -> bool:
    data = (cb.data or "").strip()

    if not data.startswith("an:"):
        return False

    chat_id = int(cb.message.chat.id)
    user_id = int(cb.from_user.id) if cb.from_user else 0
    k = _key(chat_id, user_id)

    session = _SESSIONS.get(k)
    if not session or not session.get("active"):
        await cb.answer("Сессия аналитика не найдена.")
        return True

    task = session.get("last_task")
    if not isinstance(task, ResearchTask):
        await cb.answer("Задача не найдена.")
        return True

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from analytics_agent.profiles.renovation.parser import merge_renovation_params
    from analytics_agent.profiles.renovation.report import build_renovation_report

    parts = data.split(":")
    if len(parts) < 3:
        await cb.answer("Некорректный выбор.")
        return True

    kind = parts[1]
    value = parts[2]

    if kind == "repair":
        if value not in ("economy", "middle", "premium"):
            await cb.answer("Некорректный класс ремонта.")
            return True

        task.params = merge_renovation_params(
            task.params or {},
            {"repair_class": value},
        )
        task.status = "repair_class_selected"
        _save_task(task)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Только материалы", callback_data="an:scope:materials"),
            ],
            [
                InlineKeyboardButton(text="Материалы + работы", callback_data="an:scope:materials_and_labor"),
            ],
        ])

        await cb.message.answer("Класс ремонта выбран. Теперь выбери режим расчёта:", reply_markup=kb)
        await cb.answer("Выбрано.")
        return True

    if kind == "export" and value == "xlsx":
        from aiogram.types import FSInputFile
        from analytics_agent.profiles.renovation.excel_export import build_renovation_xlsx

        export_dir = DATA_DIR / "analytics_exports"
        xlsx_path = build_renovation_xlsx(task.task_id, task.params or {}, export_dir)

        await cb.message.answer_document(
            FSInputFile(str(xlsx_path)),
            caption="Расчёт в Excel. Можно менять количества, цены и добавлять свои позиции."
        )
        await cb.answer("Файл готов.")
        return True

    if kind == "scope":
        if value not in ("materials", "materials_and_labor"):
            await cb.answer("Некорректный режим расчёта.")
            return True

        task.params = merge_renovation_params(
            task.params or {},
            {"estimate_scope": value},
        )
        task.status = "scope_selected"
        _save_task(task)

        missing_height = not task.params.get("ceiling_height")

        if missing_height:
            await cb.message.answer(
                "Режим расчёта выбран.\n\n"
                "Теперь напиши высоту потолков.\n"
                "Например: 2.7"
            )
        else:
            session["mode"] = "READY"
            result = build_renovation_report(task.task_id, task.params)
            await answer_long(cb.message, result)

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Выгрузить расчёт в Excel",
                        callback_data="an:export:xlsx",
                    )
                ]
            ])
            await cb.message.answer("Можно выгрузить расчёт в редактируемый файл:", reply_markup=kb)

        await cb.answer("Выбрано.")
        return True

    await cb.answer("Неизвестный выбор.")
    return True

async def handle_analytics_photo(message, img_bytes: bytes, answer_long) -> bool:
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else 0
    k = _key(chat_id, user_id)

    session = _SESSIONS.get(k)
    if not session or not session.get("active"):
        return False

    profile = session.get("profile")

    if profile and profile not in (
        "renovation",
        "commercial_renovation",
    ):
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

    if not session.get("profile"):
        rooms = layout.get("rooms") or []

        residential_score = 0
        commercial_score = 0

        residential_words = (
            "спаль",
            "гостин",
            "детск",
            "кух",
            "квартира",
        )

        commercial_words = (
            "офис",
            "кабинет",
            "open space",
            "торгов",
            "склад",
            "ресепшен",
            "пвз",
            "магазин",
        )

        for r in rooms:
            name = str(r.get("name") or "").strip().lower()

            if any(w in name for w in residential_words):
                residential_score += 1

            if any(w in name for w in commercial_words):
                commercial_score += 1

        if commercial_score > residential_score:
            session["profile"] = "commercial_renovation"
        else:
            session["profile"] = "renovation"

        task.profile = session["profile"]

    total_area = layout.get("total_area_m2")

    if total_area is None:
        rooms = layout.get("rooms") or []

        areas = []

        for r in rooms:
            try:
                v = r.get("area_m2")
                if v is not None:
                    areas.append(float(v))
            except Exception:
                pass

        if areas:
            total_area = round(sum(areas), 1)

    if total_area is not None and not task.params.get("area_m2"):
        task.params["area_m2"] = float(total_area)

    if not task.params.get("property_type"):
        rooms = layout.get("rooms") or []
        living_rooms = []

        for r in rooms:
            name = str(r.get("name") or "").strip().lower()
            if any(x in name for x in ("спаль", "гостин", "комнат")):
                living_rooms.append(r)

        if len(living_rooms) == 1:
            task.params["property_type"] = "one_room_apartment"
        elif len(living_rooms) == 2:
            task.params["property_type"] = "two_room_apartment"
        elif len(living_rooms) >= 3:
            task.params["property_type"] = "multi_room_apartment"

    missing = []
    if task.params.get("area_m2") is None:
        missing.append("area_m2")
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

    if session.get("profile") == "commercial_renovation":
        summary.append("Тип объекта: коммерческое помещение")
    else:
        summary.append("Тип объекта: жилое помещение")

    if total_area is not None:
        summary.append(f"Площадь: {total_area} м²")

    rooms = layout.get("rooms") or []
    if rooms:
        summary.append(f"Распознанные зоны на плане: {len(rooms)}")

    if layout.get("bathrooms") is not None:
        summary.append(f"Санузлов: {layout.get('bathrooms')}")

    summary.append("")
    summary.append(
        "Теперь выбери класс ремонта кнопкой.\n"
        "Высоту потолков потом можно будет написать текстом."
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Эконом", callback_data="an:repair:economy"),
            InlineKeyboardButton(text="Средний", callback_data="an:repair:middle"),
        ],
        [
            InlineKeyboardButton(text="Премиум", callback_data="an:repair:premium"),
        ],
    ])

    await message.answer("\n".join(summary), reply_markup=kb)
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

    if not profile and session.get("mode") == "WAIT_PROFILE":
        PROFILE_NUMBERS = {
            "1": "renovation",
            "2": "auto_parts",
            "3": "real_estate",
            "4": "electronics",
        }

        mapped = PROFILE_NUMBERS.get(raw.strip())

        if mapped:
            detected = mapped
        else:
            detected = detect_profile(raw)
    else:
        detected = detect_profile(raw)

    if detected and not profile:
        session["profile"] = detected

        if detected in ("renovation", "commercial_renovation"):
            session["mode"] = "WAIT_LAYOUT"

            await message.answer(
                f"Профиль выбран: {PROFILES[detected]['title']}.\n\n"
                "Теперь загрузи план помещения.\n"
                "Я извлеку помещения, площадь и структуру."
            )
            return True

        if detected == "auto_parts":
            session["mode"] = "WAIT_VIN"
            session["last_task"] = None

            await message.answer(
                f"Профиль выбран: {PROFILES[detected]['title']}.\n\n"
                "Напиши VIN автомобиля.\n"
                "Потом уточню марку, модель, год, двигатель, привод/кузов "
                "и какой товар или деталь анализируем."
            )
            return True

        await message.answer(
            f"Профиль выбран: {PROFILES[detected]['title']}."
        )
        return True

    if not profile:
        await message.answer(profiles_help())
        return True

    existing = session.get("last_task")

    if session.get("profile") == "auto_parts" and session.get("mode") == "WAIT_VIN":
        vin = raw.strip().upper().replace(" ", "")
        full_text = raw.strip()

        vin_match = re.search(
            r"\b([A-HJ-NPR-Z0-9]{17})\b",
            full_text,
            flags=re.I,
        )

        year_match = re.search(
            r"\b(19\d{2}|20\d{2})\b",
            full_text,
        )

        looks_full_context = (
            vin_match
            and year_match
            and (
                "," in full_text
                or "\n" in full_text
            )
        )

        if looks_full_context:
            lines = [
                x.strip(" ,")
                for x in re.split(r"[\n,]+", full_text)
                if x.strip(" ,")
            ]

            vin = vin_match.group(1).upper()

            make = ""
            model = ""
            year = year_match.group(1)
            engine = ""
            drive_body = ""
            product = ""

            non_vin = [x for x in lines if vin not in x.upper()]

            if len(non_vin) >= 1:
                make = non_vin[0]

            if len(non_vin) >= 2:
                model = non_vin[1]

            if len(non_vin) >= 3:
                engine = non_vin[3 - 1]

            if len(non_vin) >= 4:
                drive_body = non_vin[4 - 1]

            if len(non_vin) >= 5:
                product = non_vin[5 - 1]

            task = ResearchTask.create(
                profile="auto_parts",
                user_text=full_text,
                params={
                    "vin": vin,
                    "make": make,
                    "model": model,
                    "year": year,
                    "engine": engine,
                    "drive_body": drive_body,
                    "product": product,
                    "part": product,
                },
            )

            session["last_task"] = task
            session["mode"] = "RESEARCH"

            _save_task(task)

            from analytics_agent.profiles.auto_parts.report import (
                build_auto_parts_report,
            )

            result = build_auto_parts_report(
                task.task_id,
                vin,
                product,
                task.params,
            )

            task.status = "done"
            session["mode"] = "READY"

            _save_task(task)

            await answer_long(message, result)
            return True

        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            await message.answer(
                "VIN должен быть 17 символов без I, O, Q.\n"
                "Напиши VIN ещё раз."
            )
            return True

        task = ResearchTask.create(
            profile="auto_parts",
            user_text=vin,
            params={"vin": vin},
        )
        task.status = "vin_received"
        session["last_task"] = task
        session["mode"] = "WAIT_MAKE"
        _save_task(task)

        await message.answer(
            "VIN приняла.\n\n"
            "Теперь напиши марку автомобиля.\n"
            "Например: Nissan"
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_MAKE"
        and isinstance(existing, ResearchTask)
    ):
        make = raw.strip()

        if not make:
            await message.answer("Напиши марку автомобиля.")
            return True

        existing.params["make"] = make
        existing.status = "make_received"
        session["mode"] = "WAIT_MODEL"
        _save_task(existing)

        await message.answer(
            "Марку приняла.\n\n"
            "Теперь напиши модель.\n"
            "Например: X-Trail"
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_MODEL"
        and isinstance(existing, ResearchTask)
    ):
        model = raw.strip()

        if not model:
            await message.answer("Напиши модель автомобиля.")
            return True

        existing.params["model"] = model
        existing.status = "model_received"
        session["mode"] = "WAIT_YEAR"
        _save_task(existing)

        await message.answer(
            "Модель приняла.\n\n"
            "Теперь напиши год выпуска.\n"
            "Например: 2020"
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_YEAR"
        and isinstance(existing, ResearchTask)
    ):
        year_text = raw.strip()
        m = re.search(r"\b(19\d{2}|20\d{2})\b", year_text)

        if not m:
            await message.answer(
                "Напиши год выпуска числом.\n"
                "Например: 2020"
            )
            return True

        existing.params["year"] = m.group(1)
        existing.status = "year_received"
        session["mode"] = "WAIT_ENGINE"
        _save_task(existing)

        await message.answer(
            "Год приняла.\n\n"
            "Теперь напиши двигатель.\n"
            "Например: 2.0 бензин"
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_ENGINE"
        and isinstance(existing, ResearchTask)
    ):
        engine = raw.strip()

        if not engine:
            await message.answer("Напиши двигатель. Например: 2.0 бензин.")
            return True

        existing.params["engine"] = engine
        existing.status = "engine_received"
        session["mode"] = "WAIT_DRIVE_BODY"
        _save_task(existing)

        await message.answer(
            "Двигатель приняла.\n\n"
            "Теперь напиши привод и кузов/поколение, если знаешь.\n"
            "Например: полный привод, T32"
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_DRIVE_BODY"
        and isinstance(existing, ResearchTask)
    ):
        drive_body = raw.strip()

        if not drive_body:
            await message.answer(
                "Напиши привод и кузов/поколение.\n"
                "Если не знаешь — напиши: не знаю."
            )
            return True

        existing.params["drive_body"] = drive_body
        existing.status = "vehicle_context_received"
        session["mode"] = "WAIT_PRODUCT"
        _save_task(existing)

        vehicle_line = " ".join(
            str(existing.params.get(x) or "").strip()
            for x in ("make", "model", "year", "engine", "drive_body")
            if str(existing.params.get(x) or "").strip()
        )

        await message.answer(
            "Контекст авто собрала:\n"
            f"{vehicle_line}\n\n"
            "Теперь напиши, что анализируем.\n"
            "Например: задние колодки, летняя резина, литые диски, масло, аккумулятор."
        )
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_PRODUCT"
        and isinstance(existing, ResearchTask)
    ):
        product = raw.strip()

        if not product:
            await message.answer("Напиши товар или деталь, которую анализируем.")
            return True

        from analytics_agent.profiles.auto_parts.report import build_auto_parts_report

        existing.params["product"] = product
        existing.params["part"] = product
        existing.status = "researching"
        session["mode"] = "RESEARCH"
        _save_task(existing)

        result = build_auto_parts_report(
            existing.task_id,
            str((existing.params or {}).get("vin") or ""),
            product,
            existing.params,
        )

        existing.status = "done"
        session["mode"] = "READY"
        _save_task(existing)

        await answer_long(message, result)
        return True

    if (
        session.get("profile") == "auto_parts"
        and session.get("mode") == "WAIT_PART"
        and isinstance(existing, ResearchTask)
    ):
        vin = str((existing.params or {}).get("vin") or "").strip().upper()
        part = raw.strip()

        if not part:
            await message.answer("Напиши название детали, которую ищем.")
            return True

        from analytics_agent.profiles.auto_parts.report import build_auto_parts_report

        existing.params["part"] = part
        existing.status = "researching"
        session["mode"] = "RESEARCH"
        _save_task(existing)

        result = build_auto_parts_report(
            existing.task_id,
            vin,
            part,
            existing.params,
        )

        existing.status = "done"
        session["mode"] = "READY"
        _save_task(existing)

        await answer_long(message, result)
        return True

    if (
        session.get("profile") in ("renovation", "commercial_renovation")
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

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выгрузить расчёт в Excel",
                    callback_data="an:export:xlsx",
                )
            ]
        ])

        await message.answer("Можно выгрузить расчёт в редактируемый файл:", reply_markup=kb)
        return True

    last_task = session.get("last_task")

    if isinstance(last_task, ResearchTask) and _looks_like_followup(raw):
        if last_task.profile == "renovation":
            reply = _renovation_followup_reply(last_task, raw)
            await answer_long(message, reply)
            return True
    
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