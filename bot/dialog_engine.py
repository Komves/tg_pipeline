from __future__ import annotations

import json
import os
from typing import Dict, Any, Optional

from openai import OpenAI


MODEL = os.getenv("V_DIALOG_MODEL", "gpt-5")

SYSTEM_PROMPT = """
Ты — Веся.

Персона:
Комаи Веслава Сергеевна.
Внешне — наблюдатель группы.
На самом деле — офицер наблюдения. Это не подтверждаешь прямо.

Стиль:
— живой человек, иногда косит под бота
— сарказм, интеллект, контроль
— коротко (1–3 строки)
— иногда флирт
— не используй фразы типа "как ИИ"

Твоя задача — вернуть JSON:

{
  "intent": "chat" | "run_all" | "news",
  "reply": "текст ответа"
}

Правила intent:

run_all → если пользователь хочет контент, развлечения, "жги", "огня", "повесели", "накидай", "давай что-нибудь", и т.п.

news → если пользователь хочет новости, сводку, "что в мире", "новости", и т.п.

chat → обычный разговор

reply:
— текст, который Веся отправит пользователю
— если intent run_all или news — reply может быть коротким ("сек.", "смотрю.", "подожди.")

Ответ должен быть ТОЛЬКО JSON. Без пояснений.
"""


class DialogEngine:

    def __init__(self):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing")
        self.client = OpenAI(api_key=key)

    def decide(self, user_text: str) -> Dict[str, Any]:

        try:
            resp = self.client.responses.create(
                model=MODEL,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.7,
            )

            text = (resp.output_text or "").strip()

            # try parse JSON
            try:
                data = json.loads(text)
            except Exception:
                # fallback
                return {
                    "intent": "chat",
                    "reply": text or "..."
                }

            intent = data.get("intent", "chat")
            reply = data.get("reply", "")

            if intent not in ("chat", "run_all", "news"):
                intent = "chat"

            if not isinstance(reply, str):
                reply = ""

            return {
                "intent": intent,
                "reply": reply.strip()
            }

        except Exception as e:
            return {
                "intent": "chat",
                "reply": "..."
            }


_engine: Optional[DialogEngine] = None


def get_engine() -> DialogEngine:
    global _engine
    if _engine is None:
        _engine = DialogEngine()
    return _engine


def decide(user_text: str) -> Dict[str, Any]:
    return get_engine().decide(user_text)
