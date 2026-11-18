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
        rows.append([InlineKeyboardButton(text="Скачать установщик GPT", url=url)])
    rows.append([InlineKeyboardButton(text="Получить новый пароль GPT", callback_data="gpt:reset")])
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
    if re.fullmatch(r"/gpt_password(?:@\w+)?", value, re.I):
        return "password_help"
    if re.fullmatch(r"/gpt_(?:status|code|approve|paid_confirm|npd|disable|enable)(?:\s+\d+)?", value, re.I):
        return "admin"
    lower = re.sub(r"^(веся|вися|веська|vesya)\s*[,.:;!\-]?\s*", "", value.casefold()).strip()
    if re.fullmatch(
        r"(?:покажи|дай|пришли|отправь|напомни|скажи)\s+(?:мне\s+)?(?:мой\s+)?пароль\s+(?:от\s+|для\s+)?(?:gpt|гпт|chatgpt|чатгпт)"
        r"|(?:я\s+)?(?:забыл|забыла|потерял|потеряла|не помню)\s+пароль\s+(?:от\s+|для\s+)?(?:gpt|гпт|chatgpt|чатгпт)"
        r"|(?:где\s+)?(?:мой\s+)?пароль\s+(?:от\s+|для\s+)?(?:gpt|гпт|chatgpt|чатгпт)"
        r"|(?:как\s+)?(?:восстановить|получить|узнать)\s+пароль\s+(?:от\s+|для\s+)?(?:gpt|гпт|chatgpt|чатгпт)",
        lower.rstrip(" .!?"),
    ):
        return "password_help"
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


def _credential_instructions(login: str, password: str, *, repeat: bool = False) -> str:
    """Send identical, valid connection details without changing the password."""
    url = _installer_url()
    return (
        ("GPT уже подключён. Повторная оплата не нужна.\n\n" if repeat
         else "Индивидуальное подключение GPT создано.\n\n")
        + f"Логин: {login}\nПароль: {password}\n\n"
        + (f"Скачать GPT-Setup.exe:\n{url}\n\n"
           if url else "Установщик пока не опубликован.\n\n")
        + "Запусти GPT-Setup.exe и установи программу. Затем открой ярлык GPT, "
          "введи логин и пароль один раз. Программа использует отдельный профиль Chrome. "
          "При следующих запусках используй ярлык GPT.\n\n"
          "Не пересылай пароль другим людям."
    )


async def _send_active_details(message, app, reg):
    """Send the SAME active password if it was securely saved at issue time."""
    uid = int(message.from_user.id)
    login = str(app.get("proxy_username") or "")
    if not login.startswith("gpt_"):
        await message.answer("Доступ GPT отмечен как оформленный, но логин не найден. Обратись к владельцу.")
        return
    try:
        saved = reg.get_credential(uid)
    except Exception:
        LOG.warning("GPT credential vault unavailable; no credentials logged")
        await message.answer(
            "Не удалось безопасно получить сохранённые данные GPT. "
            "Обратись к владельцу. Повторная оплата не требуется."
        )
        return
    if saved is not None:
        username, password = saved
        try:
            status = await GPTAgentClient(GPTAgentConfig.from_env()).get_user_status(uid)
        except Exception:
            LOG.warning("GPT account verification failed on repeat request")
            await message.answer("Не удалось проверить действующий доступ GPT. Повтори запрос позже.")
            return
        if status.get("status") != "active" or status.get("username") != username or username != login:
            await message.answer(
                "Доступ GPT сейчас неактивен или изменён. Обратись к владельцу."
            )
            return
        await message.answer(
            _credential_instructions(username, password, repeat=True),
            reply_markup=_active_keyboard(),
        )
        return

    # Account was issued before we started securely escrowing credentials.
    # Never silently rotate: that would disrupt existing Windows installations.
    url = _installer_url()
    await message.answer(
        "GPT уже подключён. Повторная оплата не нужна.\n\n"
        f"Твой логин: {login}\n\n"
        + (f"Скачать GPT-Setup.exe:\n{url}\n\n" if url else "")
        + "Этот аккаунт создан до внедрения повторной выдачи пароля. "
          "Прежний пароль зашифрованным способом не сохранялся, поэтому "
          "вернуть его из хеша невозможно. Один раз нажми «Получить новый пароль GPT»: "
          "после этого Веся будет показывать один и тот же действующий пароль "
          "при каждом повторном запросе. Старый пароль перестанет работать.",
        reply_markup=_active_keyboard(),
    )


async def _provision(message, uid: int, *, owner_approved: bool = False):
    reg = registry()
    app = reg.get(uid)
    if not app or app["stage"] != "provisioning" or app.get("method") not in ("paid", "free"):
        return
    if app.get("method") == "paid" and not app.get("payment_confirmed"):
        # Defense in depth: the account must never be created on receipt OCR alone.
        return
    try:
        client = GPTAgentClient(GPTAgentConfig.from_env())
        stored = reg.get_credential(uid)
        if stored:
            login, password = stored
            status = await client.get_user_status(uid)
            if status.get("status") != "active" or status.get("username") != login:
                raise ValueError("Previously escrowed GPT credentials are no longer active")
        else:
            result = await client.create_user(uid)
            password = result.get("initial_password")
            if not password:
                # Agent lost the one-time initial secret; recover ONCE, then escrow.
                result = await client.rotate_password(uid)
                password = result.get("initial_password")
            login = str(result.get("username") or "")
            if not login.startswith("gpt_") or not password:
                raise ValueError("Missing credentials")
            # Persist encrypted BEFORE Telegram delivery; retry uses this password.
            reg.save_credential(uid, login, password)
        instructions = _credential_instructions(login, password)
        # Owner callback runs in the OWNER chat, but credentials must go
        # exclusively to the client's private Telegram ID.
        if owner_approved:
            await message.bot.send_message(uid, instructions)
        else:
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
        if owner_approved:
            try:
                await message.bot.send_message(
                    uid,
                    "Владелец одобрил подключение, но выдача пока не завершилась. "
                    "Напиши /gpt, повторная оплата не требуется.",
                )
            except Exception:
                LOG.warning("GPT failed provisioning client notification")
            await message.answer(
                f"Подключение GPT для {uid} одобрено, но выдача пока не удалась. "
                "Клиент может повторить /gpt без оплаты."
            )
        else:
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
        await _send_active_details(message, app, reg)
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
    elif command in ("/gpt_approve", "/gpt_paid_confirm"):
        if not reg.mark_payment(uid):
            await message.answer("Подтверждение не принято: квитанции нет или доступ уже одобрен.")
            return
        await message.answer(f"Подключение GPT для {uid} одобрено. Создаю доступ.")
        await _provision(message, uid, owner_approved=True)
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
    elif intent == "password_help":
        app = registry().get(uid)
        if not app:
            await message.answer("GPT ещё не оформлен. Напиши «Веся, подключи мне GPT».")
        elif app.get("stage") != "active":
            await message.answer("Подключение GPT ещё не завершено. Напиши /gpt. Повторная оплата не нужна.")
        else:
            await _send_active_details(message, app, registry())
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
    if data.startswith("gpt:payapprove:"):
        if not is_admin or int(callback.from_user.id) != owner_id():
            await callback.answer("Нет доступа", show_alert=True)
            return True
        try:
            _, _, uid_text, receipt_prefix = data.split(":")
            uid = int(uid_text)
        except (ValueError, IndexError):
            await callback.answer("Неверная заявка", show_alert=True)
            return True
        if uid <= 0 or uid >= 2**63 or not re.fullmatch(r"[0-9a-f]{16}", receipt_prefix):
            await callback.answer("Неверная заявка", show_alert=True)
            return True
        reg = registry()
        app = reg.get(uid)
        if not app or not str(app.get("receipt_hash") or "").startswith(receipt_prefix):
            await callback.answer("Квитанция уже заменена. Проверь новую.", show_alert=True)
            return True
        if not reg.mark_payment(uid, receipt_prefix=receipt_prefix):
            await callback.answer("Уже одобрено или нет ожидающей квитанции", show_alert=True)
            return True
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            LOG.warning("GPT owner approval markup not updated")
        await callback.answer("Подключение одобрено")
        await callback.message.answer(f"Подключение GPT для {uid} одобрено. Создаю доступ.")
        await _provision(callback.message, uid, owner_approved=True)
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
        # Prevent a second tap on an old confirmation button from rotating
        # the just-issued password again.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
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
                # Make subsequent /gpt requests return this exact password.
                registry().save_credential(uid, login, password)
                await callback.message.answer(
                    f"Новый пароль GPT для {login}:\n{password}\n\n"
                    "Старый пароль больше не действует.\n\n"
                    "Если GPT ещё не установлен, запусти GPT-Setup.exe, затем ярлык GPT "
                    "и введи новый пароль.\n\n"
                    "Если GPT уже работал на компьютере: перезагрузи Windows "
                    "(чтобы закрыть старый прокси), затем открой PowerShell "
                    "и выполни команду:\n"
                    "& \"$env:LOCALAPPDATA\\GPT\\GPT.exe\" --reset\n\n"
                    "Введи логин и новый пароль. Не пересылай их посторонним."
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
