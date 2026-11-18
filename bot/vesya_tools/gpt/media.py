"""Receive payment receipts in a GPT-only private conversation.

Telegram file ID is preserved so the owner receives the original receipt.
Only the owner's explicit one-click approval permits issuing paid access.
"""
from __future__ import annotations

import hashlib
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .handler import _notify_owner, owner_id, registry
from .receipts import MAX_BYTES, analyze_receipt, extract_receipt

LOG = logging.getLogger("vesya.gpt.receipts")


async def handle_gpt_media(message) -> bool:
    if not message.from_user or message.chat.type != "private":
        return False
    uid = int(message.from_user.id)
    app = registry().get(uid)
    if not app or app["stage"] not in ("awaiting_receipt", "receipt_review"):
        return False
    item = message.document or (message.photo[-1] if message.photo else None)
    if item is None:
        return False
    mime = getattr(item, "mime_type", "") or ("image/jpeg" if message.photo else "")
    filename = getattr(item, "file_name", "") or ("receipt.jpg" if message.photo else "")
    if mime not in ("application/pdf", "image/jpeg", "image/png", "image/webp") and not filename.lower().endswith(".pdf"):
        await message.answer("Пришли банковскую квитанцию PDF, JPG или PNG.")
        return True
    if getattr(item, "file_size", 0) > MAX_BYTES:
        await message.answer("Слишком большой файл (не более 10 МБ).")
        return True

    try:
        file_ref = await message.bot.get_file(item.file_id)
        stream = await message.bot.download_file(file_ref.file_path)
        raw = stream.read()
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("Invalid receipt size")
        digest = hashlib.sha256(raw).hexdigest()
        extracted = await extract_receipt(raw, filename, mime)
        accepted, txn, amount = analyze_receipt(extracted)
        saved = registry().record_receipt(
            uid, file_id=item.file_id, kind="photo" if message.photo else "document",
            sha256=digest, txn=txn, bank=str(extracted.get("bank") or ""),
            date=str(extracted.get("date") or ""), amount=amount,
            recipient=str(extracted.get("recipient") or ""), accepted=accepted,
        )
        if not saved:
            await message.answer("Квитанция уже использовалась или заявка закрыта. Свяжись с владельцем.")
            return True

        # Original receipt reaches the owner before the approval button.
        # OCR is advisory; it NEVER authorizes paid access on its own.
        try:
            if message.photo:
                await message.bot.send_photo(owner_id(), item.file_id)
            else:
                await message.bot.send_document(owner_id(), item.file_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="Подтвердить подключение",
                    callback_data=f"gpt:payapprove:{uid}:{digest[:16]}",
                )
            ]])
            await _notify_owner(
                message.bot,
                f"Новая квитанция по консультации GPT.\nTelegram ID: {uid}\n"
                f"Сумма по квитанции: {amount} ₽\n"
                f"Дата: {str(extracted.get('date') or 'не указана')[:50]}\n"
                f"Банк: {str(extracted.get('bank') or 'не указан')[:100]}\n"
                f"Номер операции: {str(extracted.get('transaction_id') or 'не указан')[:100]}\n"
                f"Получатель: {str(extracted.get('recipient') or 'не указан')[:100]}\n"
                f"Проверка реквизитов: {'совпадают' if accepted else 'требуют проверки'}.\n\n"
                "Проверь поступление самостоятельно. Нажатие кнопки одобряет "
                "подключение клиента и выдаёт ему доступ. Чек НПД отдельно.",
                reply_markup=kb,
            )
        except Exception:
            await message.answer(
                "Квитанция сохранена, но уведомить владельца не удалось. "
                "Повторно платить не нужно."
            )
            return True
        await message.answer(
            "Квитанция получена и направлена владельцу. "
            "Подключение выполняется только после его подтверждения. "
            "Повторная оплата не нужна."
        )
    except Exception:
        LOG.warning("GPT receipt error; no banking data or receipt bytes logged")
        await message.answer(
            "Не получилось обработать квитанцию. Отправь её повторно как PDF или фото. "
            "Повторно оплачивать не нужно."
        )
    return True
