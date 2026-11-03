"""Vesya's isolated temporary proxy commands. No LLM routing or name collection."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

LOG = logging.getLogger("vesya.proxy")
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
SERVICE_HOST = "paruslegal.ru"
SERVICE_PORT = 43128


def parse_proxy_intent(text: str):
    """Only explicit proxy requests are intercepted; unrelated chat stays untouched."""
    text = (text or "").strip().lower()
    text = re.sub(r"^(веся|вися|веська|vesya)\s*[,.:;!\-]?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ?!.,:;")
    if re.fullmatch(r"/start(?:@\w+)? proxy", text):
        return "register", None
    if re.fullmatch(r"/proxy(?:@\w+)?|(?:дай|выдай|получить|подключить|хочу|нужен|нужна|оформи)\s+(?:мне\s+)?(?:временный\s+)?прокси|прокси", text):
        return "register", None
    if re.fullmatch(r"/proxy_status(?:@\w+)?|(?:мой|статус|срок)\s+прокси", text):
        return "status", None
    if re.fullmatch(r"/proxy_admin(?:@\w+)?|(?:покажи|дай|список)\s+(?:всех\s+)?(?:пользователей|список пользователей)\s+прокси|список\s+прокси", text):
        return "list", None
    m = re.fullmatch(r"(?:/proxy_renew(?:@\w+)?|продли\s+прокси)\s+(\d{1,6})(?:\s+на\s+месяц)?", text)
    if m:
        return "renew", int(m.group(1))
    m = re.fullmatch(r"(?:/proxy_disable(?:@\w+)?|отключи\s+прокси)\s+(\d{1,6})", text)
    if m:
        return "disable", int(m.group(1))
    return None


async def _api(action: str, payload: dict) -> dict:
    base = (os.getenv("GUEST_PROXY_API_URL") or "").strip().rstrip("/")
    token = (os.getenv("GUEST_PROXY_API_TOKEN") or "").strip()
    if not base.startswith("https://") or not token:
        raise RuntimeError("Proxy API is not configured")
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.post(
            f"{base}/v1/{action}",
            headers={"X-Proxy-Token": token},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid API reply")
        return data


def _date(unix_time: int) -> str:
    return datetime.fromtimestamp(int(unix_time), TIMEZONE).strftime("%d.%m.%Y %H:%M (Екб)")


def _instructions(result: dict) -> str:
    number = int(result["id"])
    login = str(result["username"])
    password = str(result["password"])
    return (
        f"Прокси №{number:03d} активирован на 30 дней.\n"
        f"Доступ до: {_date(result['expires_at'])}\n\n"
        f"Адрес: {SERVICE_HOST}\n"
        f"Порт: {SERVICE_PORT}\n"
        f"Тип: HTTPS-прокси\n"
        f"Логин: {login}\n"
        f"Пароль: {password}\n\n"
        "Для Windows открой PowerShell и выполни:\n"
        '& "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe" '
        '--user-data-dir="$env:LOCALAPPDATA\\VesyaProxyChrome" '
        f'--proxy-server="https://{SERVICE_HOST}:{SERVICE_PORT}" '
        '"https://chatgpt.com/"\n\n'
        "При запросе введи логин и пароль выше.\n"
        "Доступ автоматически прекратится через 30 дней. "
        "Повторная регистрация новый срок не даёт."
    )


def _format_list(items: list[dict]) -> str:
    if not items:
        return "Пока нет пользователей прокси."
    lines = ["Прокси — пользователи:"]
    labels = {"active": "активен", "expired": "истёк", "disabled": "отключён"}
    for row in items[:100]:
        lines.append(
            f"№{int(row['id']):03d} — {labels.get(row['status'], row['status'])} "
            f"до {_date(row['expires_at'])}"
        )
    if len(items) > 100:
        lines.append(f"... ещё {len(items) - 100}")
    lines.append("\nПродлить: /proxy_renew 3\nОтключить: /proxy_disable 3")
    return "\n".join(lines)


async def handle_proxy_message(message, raw_text: str, *, is_admin: bool = False) -> bool:
    intent = parse_proxy_intent(raw_text)
    if intent is None:
        return False
    action, number = intent
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("Прокси выдаю только в личном чате.")
        return True
    if action in {"list", "renew", "disable"} and not is_admin:
        await message.answer("Эта команда доступна только администратору.")
        return True

    try:
        if action in {"register", "status"}:
            result = await _api(action, {"telegram_id": int(message.from_user.id)})
            state = result.get("status")
            if state == "not_found":
                await message.answer("Ты пока не подключал прокси. Напиши /proxy.")
            elif state == "active":
                await message.answer(_instructions(result))
            elif state == "expired":
                await message.answer(
                    f"Прокси №{int(result['id']):03d}: срок закончился "
                    f"{_date(result['expires_at'])}. Повторно месяц не выдаётся."
                )
            elif state == "disabled":
                await message.answer(f"Прокси №{int(result['id']):03d} отключён.")
            else:
                raise ValueError("Unexpected proxy state")
        elif action == "list":
            data = await _api("list", {})
            await message.answer(_format_list(data.get("users", [])))
        elif action in {"renew", "disable"}:
            result = await _api(action, {"id": int(number)})
            label = "продлён" if action == "renew" else "отключён"
            await message.answer(
                f"Прокси №{int(number):03d} {label}. "
                f"Срок: {_date(result['expires_at'])}. "
                "Изменение применяется автоматически в течение минуты."
            )
    except Exception as exc:
        LOG.warning("proxy operation failed action=%s type=%s", action, type(exc).__name__)
        await message.answer(
            "Сервис прокси пока недоступен. Попробуй позже или сообщи администратору."
        )
    return True
