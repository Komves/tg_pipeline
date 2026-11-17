"""Receipt extraction; no payment is 'confirmed' until owner reconciles bank account."""
import asyncio
import base64
import hashlib
import json
import os
import re

PRICE = 2990
MAX_BYTES = 10 * 1024 * 1024


def _extract_sync(raw: bytes, filename: str, mime: str) -> dict:
    from openai import OpenAI
    image, media = raw, mime or "image/jpeg"
    if media == "application/pdf" or filename.lower().endswith(".pdf"):
        import fitz
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            if len(pdf) < 1 or len(pdf) > 5:
                raise ValueError("Unsupported PDF")
            pix = pdf[0].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
            image, media = pix.tobytes("png"), "image/png"
    if media not in ("image/jpeg", "image/png", "image/webp"):
        raise ValueError("Unsupported image")
    uri = "data:" + media + ";base64," + base64.b64encode(image).decode("ascii")
    response = OpenAI().chat.completions.create(
        model=os.getenv("GPT_RECEIPT_MODEL", "gpt-4o-mini"),
        temperature=0,
        max_tokens=450,
        messages=[
            {"role": "system", "content": "Extract literally visible bank receipt fields. Return ONLY JSON."},
            {"role": "user", "content": [
                {"type": "text", "text":
                    "Прочитай банковскую квитанцию. Только явно видимое. "
                    "Верни JSON: amount_rub (целые рубли), bank, transaction_id, "
                    "date, recipient, completed (true только если явно написано исполнено/успешно). "
                    "Ничего не придумывай."},
                {"type": "image_url", "image_url": {"url": uri}},
            ]},
        ],
    )
    raw_text = (response.choices[0].message.content or "").strip()
    if raw_text.startswith("json\n"):
        raw_text = raw_text[5:]
    if raw_text.startswith("'''"):
        raw_text = raw_text.strip("' \n")
    result = json.loads(raw_text)
    if not isinstance(result, dict):
        raise ValueError("Invalid receipt extraction")
    return result


async def extract_receipt(raw: bytes, filename: str, mime: str) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Receipt is too large or empty")
    return await asyncio.to_thread(_extract_sync, raw, filename, mime)


def analyze_receipt(fields: dict) -> tuple[bool, str | None, int]:
    """Without configured recipient or transaction ID => manual review."""
    raw_amount = re.sub(r"[^\d]", "", str(fields.get("amount_rub") or ""))
    amount = int(raw_amount) if raw_amount else 0
    bank = str(fields.get("bank") or "").strip().casefold()
    txn = re.sub(r"[^a-z0-9а-я_-]", "", str(fields.get("transaction_id") or "").casefold())[:100]
    payee = (os.getenv("GPT_PAYMENT_RECIPIENT") or "").strip().casefold()
    recipient = str(fields.get("recipient") or "").strip().casefold()
    key = hashlib.sha256((bank + "|" + txn).encode()).hexdigest() if bank and txn else None
    accepted = bool(
        amount == PRICE and fields.get("completed") is True
        and payee and payee in recipient and key
    )
    return accepted, key, amount
