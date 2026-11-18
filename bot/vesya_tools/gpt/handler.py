"""Vesya GPT onboarding. New /gpt flow is separate from the existing /proxy."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .client import GPTAgentClient, GPTAgentConfig
from .registry import GPTRegistry

LOG = logging.getLogger("vesya.gpt")
CODE_RE = re.compile(r"^GPT-[0-9A-F]{12}-[0-9A-F]{12}$", re.I)


def registry() -> GPTRegistry:
    return GPTRegistry(Path(os.getenv("DATA_DIR", "/data")) / "gpt_consultations.sqlite3")


def owner_id() -> int | None:
    raw = (os.getenv("GPT_OWNER_TG_ID") or "").strip() or (os.getenv("ADMIN_USER_IDS") or "").split(",")[0].strip()
    try:
        uid = int(raw)
        return uid if uid > 0 else None
    except ValueError:
        return None


def _payment_text() -> str:
    details = (os.getenv("GPT_PAYMENT_DETAILS") or "").strip()
    base = "Индивидуальная консультация, первоначальная настройка ChatGPT — 2 990 ₽, разово.\n\n"
    if not details:
        return base + "Реквизиты пока не настроены. Не переводите деньги. Если у тебя есть код от владельца, можешь использовать его."
    return base + "Реквизиты:\n" + details[:700] + "\n\nПосле оплаты загрузи банковскую квитанцию PDF или фото."


def _installer_url() -> str:
    """Return only a valid HTTPS download URL, never an unchecked relative link."""
    raw = (os.getenv("GPT_INSTALLER_URL") or "").strip()
    url = urlsplit(raw)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        return ""
    return raw


def _active_keyboard() -> InlineKeyboardMarkup:
    """Existing customer can download the app or explicitly reset lost credentials."""
    rows = []
    url = _installer_url()
    if url:
        rows.append([InlineKeyboardButton(text="Скачать GPT.exe", url=url)])
    rows.append([InlineKeyboardButton(text="Восстановить пароль GPT", callback_data="gpt:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _reset_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, изменить пароль", callback_data="gpt:reset_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="gpt:reset_cancel")],
    ])


def _choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я оплатил — загрузить квитанцию", callback_data="gpt:paid")],
        [InlineKeyboardButton(text="У меня есть секретный код", callback_data="gpt:code")],
    ])


def _owner_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Выдать бесплатный код", callback_data=f"gpt:issue:{uid}")]
    ])


def parse_intent(text: str) -> str | None:
    value = (text or "").strip()
    if CODE_RE.fullmatch(value):
        return "redeem"
    if re.fullmatch(r"/gpt_redeem(?:@\w+)?\s+GPT-[0-9A-F]{12}-[0-9A-F]{12}", value, re.I):
        return "redeem"
    if re.fullmatch(r"/gpt(?:@\w+)?", value, re.I):
        return "register"
    if re.fullmatch(r"/gpt_(?:status|code|approve|paid_confirm|npd|disable|enable)(?:\s+\d+)?", value, re.I):
        return "admin"
    lower = re.sub(r"^(веся|вися|веська|vesya)\s*[,.:;!\-]?\s*", "", value.casefold()).strip()
    if re.fullmatch(
        r"(?:подключи|подключить|хочу подключить|оформи|помоги подключить|нужно подключить)"
        r"\s+(?:мне\s+)?(?:к\s+)?(?:chatgpt|чатгпт|гпт|gpt)[.!?]?"
        r"|(?:консультация|подключение)\s+(?:chatgpt|чатгпт|гпт|gpt)[.!?]?", lower,
    ):
        return "register"
    return None


async def _notify_owner(bot, text: str, reply_markup=None):
    uid = owner_id()
    if uid is None:
        raise RuntimeError("No GPT_OWNER_TG_ID or ADMIN_USER_IDS")
    await bot.send_message(uid, text, reply_markup=reply_markup)


async def _provision(message, uid: int):
    reg = registry()
    app = reg.get(uid)
    if not app or app["stage"] != "provisioning" or app.get("method") not in ("paid", "free"):
        return
    if app.get("method") == "paid" and not app.get("payment_confirmed"):
        # Defense in depth: the account must never be created on receipt OCR alone.
        return
    try:
        client = GPTAgentClient(GPTAgentConfig.from_env())
        result = await client.create_user(uid)
        password = result.get("initial_password")
        if not password:
            # Recover lost first-time response with a controlled rotation.
            result = await client.rotate_password(uid)
            password = result.get("initial_password")
        login = str(result.get("username") or "")
        if not login.startswith("gpt_") or not password:
            raise ValueError("Missing credentials")
        url = (os.getenv("GPT_INSTALLER_URL") or "").strip()
        if not url or urlsplit(url).scheme != "https":
            url = ""
        instructions = (
            "Индивидуальное подключение GPT создано.\n\n"
            f"Логин прокси: {login}\nПервичный пароль: {password}\n\n"
            + (f"Скачать установщик GPT:\n{url}\n\n"
               "Запусти скачанный GPT.exe. Он создаст ярлык GPT, попросит логин и пароль в окне Windows и откроет отдельный Chrome. При следующем запуске используй ярлык GPT."
               if url else "Установщик пока не опубликован. Ссылка появится после проверки пакета.")
            + "\n\nНе пересылай пароль другим людям."
        )
        await message.answer(instructions)
        reg.note_provisioned(uid, login)
        try:
            await _notify_owner(
                message.bot,
                f"Доступ GPT выдан. Telegram ID: {uid}. Способ: {app['method']}. Логин: {login}. Пароль не включён."
            )
        except Exception:
            LOG.warning("GPT owner notification failed after provisioning")
    except Exception:
        LOG.warning("GPT provisioning error, no credentials logged")
        await message.answer("Заявка сохранена, но подключение пока не завершилось. Напиши /gpt позже — повторная оплата не требуется.")


async def _register(message):
    uid = int(message.from_user.id)
    reg = registry()
    app, fresh = reg.register(uid)
    if fresh:
        try:
            await _notify_owner(
                message.bot,
                f"Новая заявка на подключение GPT.\nTelegram ID: {uid}\n"
                "Услуга: консультация и настройка — 2 990 ₽.\n"
                "Статус: ожидает квитанцию или бесплатный код.",
                _owner_keyboard(uid),
            )
        except Exception:
            LOG.warning("GPT registration owner notification failed")
    if app["stage"] == "active":
        login = str(app.get("proxy_username") or "")
        url = _installer_url()
        if not login.startswith("gpt_"):
            await message.answer(
                "GPT отмечен как оформленный, но учётные данные в реестре не найдены. "
                "Обратись к владельцу: повторно оплачивать не нужно."
            )
            return
        instructions = (
            "GPT уже оформлен — повторная оплата не нужна.\n\n"
            f"Твой логин: {login}\n\n"
            + (f"Скачать установщик GPT.exe:\n{url}\n\n" if url
               else "Установщик пока не опубликован.\n\n")
            + "После установки запускай отдельный ярлык GPT. "
            "Если пароль из предыдущего сообщения потерян, нажми "
            "«Восстановить пароль GPT» — старый пароль перестанет работать."
        )
        await message.answer(instructions, reply_markup=_active_keyboard())
    elif app["stage"] == "provisioning":
        await _provision(message, uid)
    elif app["stage"] == "receipt_review":
        await message.answer("Квитанция у владельца на проверке. Повторно оплачивать не нужно.")
    else:
        await message.answer(_payment_text(), reply_markup=_choice())


async def _admin(message, text: str, is_admin: bool):
    if not is_admin or int(message.from_user.id) != owner_id():
        await message.answer("Команда только для владельца.")
        return
    parts = text.strip().split()
    command = parts[0].split("@", 1)[0].lower()
    if command == "/gpt_status":
        try:
            info = await GPTAgentClient(GPTAgentConfig.from_env()).get_service_status()
            await message.answer(f"GPT Agent: {info.get('status')}; учётных записей: {info.get('managed_accounts')}")
        except Exception:
            await message.answer("GPT Agent пока недоступен.")
        return
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Укажи Telegram ID. Например: /gpt_code 123456789")
        return
    uid = int(parts[1])
    reg = registry()
    if command == "/gpt_code":
        try:
            code = reg.issue_free_code(uid, int(message.from_user.id))
        except ValueError:
            await message.answer("Сначала человек должен обратиться к Весе с /gpt.")
            return
        await message.answer(f"Бесплатный код GPT для Telegram ID {uid}:\n{code}\n\nОдноразовый, действует 7 дней. Передай лично.")
    elif command == "/gpt_approve":
        if reg.mark_receipt_review_approved(uid):
            await message.answer(
                "Реквизиты квитанции одобрены. Доступ НЕ выдан. "
                f"После проверки поступления денег в банке напиши /gpt_paid_confirm {uid}."
            )
        else:
            await message.answer("Квитанция на ручной проверке не найдена.")
    elif command == "/gpt_paid_confirm":
        ok = reg.mark_payment(uid)
        if not ok:
            await message.answer(
                "Подтвердить нельзя: нет проверенной квитанции, уже отмечено "
                "поступление либо заявка не ожидает сверки."
            )
        else:
            await message.answer(
                "Поступление отмечено как сверенное. Клиенту направлено "
                "приглашение завершить настройку (если Telegram доступен)."
            )
            try:
                await message.bot.send_message(
                    uid,
                    "Владелец подтвердил поступление оплаты. Напиши /gpt, "
                    "чтобы получить доступ и инструкцию.",
                )
            except Exception:
                LOG.warning("GPT bank confirmation client notification failed")
    elif command == "/gpt_npd":
        ok = reg.mark_payment(uid, tax=True)
        await message.answer(
            "Выдача чека НПД отмечена." if ok
            else "Сначала подтверди поступление денег; либо отметка уже сохранена."
        )
    elif command in ("/gpt_disable", "/gpt_enable"):
        app = reg.get(uid)
        if not app or not app.get("proxy_username"):
            await message.answer("Выданный GPT-доступ не найден.")
            return
        try:
            client = GPTAgentClient(GPTAgentConfig.from_env())
            result = await (client.disable_user(uid) if command == "/gpt_disable" else client.enable_user(uid))
            await message.answer(f"GPT {uid}: {result.get('status')} (ответ сервера).")
        except Exception:
            await message.answer("Сервер не подтвердил операцию. Статус не изменён в интерфейсе.")
    else:
        await message.answer("Неизвестная команда GPT.")


async def handle_gpt_message(message, raw_text: str, *, is_admin: bool = False) -> bool:
    intent = parse_intent(raw_text)
    if intent is None:
        return False
    if not message.from_user or message.chat.type != "private":
        await message.answer("GPT оформляю только в личном чате.")
        return True
    uid = int(message.from_user.id)
    if intent == "register":
        await _register(message)
    elif intent == "admin":
        await _admin(message, raw_text, is_admin)
    elif intent == "redeem":
        code = raw_text.strip().split()[-1].upper()
        reg = registry()
        reg.register(uid)
        if reg.redeem_free_code(uid, code):
            await message.answer("Бесплатный код принят. Готовлю подключение.")
            await _provision(message, uid)
        else:
            await message.answer("Код неверен, просрочен, выдан другому пользователю или уже использован.")
    return True


async def handle_gpt_callback(callback, *, is_admin: bool = False) -> bool:
    data = callback.data or ""
    if not data.startswith("gpt:"):
        return False
    if not callback.from_user or callback.message.chat.type != "private":
        await callback.answer("Только личный чат", show_alert=True)
        return True
    if data.startswith("gpt:issue:"):
        if not is_admin or int(callback.from_user.id) != owner_id():
            await callback.answer("Нет доступа", show_alert=True)
            return True
        try:
            uid = int(data.split(":")[2])
            code = registry().issue_free_code(uid, int(callback.from_user.id))
        except (IndexError, ValueError):
            await callback.answer("Заявка не найдена", show_alert=True)
            return True
        await callback.message.answer(
            f"Одноразовый код для Telegram ID {uid}:\n{code}\n\n"
            "Действует 7 дней. Передай лично. Предыдущий код отозван."
        )
        await callback.answer("Код создан")
        return True
    if data in ("gpt:reset", "gpt:reset_confirm", "gpt:reset_cancel"):
        uid = int(callback.from_user.id)
        app = registry().get(uid)
        if not app or app["stage"] != "active" or not str(app.get("proxy_username") or "").startswith("gpt_"):
            await callback.answer("Действующий GPT-доступ не найден", show_alert=True)
            return True
        if data == "gpt:reset_cancel":
            await callback.answer("Отменено")
            return True
        if data == "gpt:reset":
            await callback.message.answer(
                "Восстановление доступа заменит текущий пароль GPT новым. "
                "Старый перестанет работать, а на компьютере придётся ввести новый пароль. "
                "Продолжить?",
                reply_markup=_reset_confirmation_keyboard(),
            )
            await callback.answer()
            return True
        try:
            client = GPTAgentClient(GPTAgentConfig.from_env())
            status = await client.get_user_status(uid)
            if status.get("status") != "active" or status.get("username") != app.get("proxy_username"):
                await callback.message.answer(
                    "GPT-доступ отключён либо данные не совпадают. "
                    "Для восстановления обратитесь к владельцу."
                )
            else:
                result = await client.rotate_password(uid)
                password = result.get("initial_password")
                login = result.get("username")
                if result.get("status") != "active" or login != app.get("proxy_username") or not password:
                    raise ValueError("Unexpected password rotation response")
                await callback.message.answer(
                    f"Новый пароль GPT для {login}:\n{password}\n\n"
                    "Старый пароль больше не действует. "
                    "На компьютере запусти GPT.exe с параметром --reset, "
                    "чтобы ввести новые данные. Не пересылай пароль посторонним."
                )
        except Exception:
            LOG.warning("GPT password recovery failed; no credentials logged")
            await callback.message.answer(
                "Не удалось подтвердить восстановление пароля. "
                "Если пароль уже изменился, повтори восстановление."
            )
        await callback.answer()
        return True
    if data not in ("gpt:paid", "gpt:code"):
        await callback.answer("Неизвестное действие", show_alert=True)
        return True
    uid = int(callback.from_user.id)
    reg = registry()
    app = reg.get(uid)
    if not app:
        await callback.answer("Начни с /gpt", show_alert=True)
        return True
    if app["stage"] in ("active", "provisioning"):
        await callback.answer("Заявка уже оформлена", show_alert=True)
        return True
    if data == "gpt:paid":
        if not (os.getenv("GPT_PAYMENT_DETAILS") or "").strip():
            await callback.message.answer("Реквизиты ещё не настроены. Не переводите деньги.")
        else:
            reg.set_stage(uid, "awaiting_receipt")
            await callback.message.answer("Пришли банковскую квитанцию PDF или фото. Без квитанции доступ не выдаю.")
    else:
        await callback.message.answer("Введи одноразовый код, который владелец сообщил лично.")
    await callback.answer()
    return True
