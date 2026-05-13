from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict

from openai import OpenAI


def _safe_json(text: str) -> Dict[str, Any]:
    try:
        m = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
        if not m:
            return {}
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_layout_from_image(img_bytes: bytes) -> Dict[str, Any]:
    if not img_bytes:
        return {"status": "empty_image", "rooms": []}

    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return {"status": "no_openai_key", "rooms": []}

    try:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        client = OpenAI()

        resp = client.responses.create(
            model=os.getenv("V_VISION_MODEL", os.getenv("V_DIALOG_MODEL", "gpt-5.4-mini")),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Ты извлекаешь структуру квартиры из плана/планировки.\n"
                        "Не оценивай ремонт и не рассуждай.\n"
                        "Верни только JSON.\n\n"
                        "Формат:\n"
                        "{"
                        "\"status\":\"ok|uncertain\","
                        "\"total_area_m2\": число или null,"
                        "\"rooms\":[{\"name\":\"строка\",\"area_m2\":число или null}],"
                        "\"bathrooms\": число или null,"
                        "\"balcony\": true/false/null,"
                        "\"doors_count\": число или null,"
                        "\"notes\":[\"строка\"]"
                        "}\n\n"
                        "Если площадь помещения не читается — ставь null. "
                        "Не выдумывай точные площади, если их нет на плане.\n\n"
                        "КРИТИЧНО:\n"
                        "1. total_area_m2 — это только общая площадь объекта, НЕ площадь отдельной комнаты.\n"
                        "2. Ищи общую площадь в верхних надписях, заголовках, строках со словами: площадь, общая, проектная.\n"
                        "3. Если видишь диапазон общей площади вида 40,7–42,1 м² — верни верхнюю границу диапазона как total_area_m2.\n"
                        "4. Если рядом с числом указано название помещения: кухня, спальня, санузел, лоджия, холл — это площадь комнаты, НЕ total_area_m2.\n"
                        "5. Таблицу помещений используй для rooms, а не для total_area_m2.\n"
                        "6. Перед ответом проверь: total_area_m2 не должен быть меньше суммы площадей rooms.\n"
                        "7. Если общая площадь не читается уверенно — ставь total_area_m2 null."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Извлеки JSON по плану квартиры."},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}",
                        },
                    ],
                },
            ],
        )

        text = getattr(resp, "output_text", "") or ""
        data = _safe_json(text)

        if not data:
            return {"status": "parse_failed", "rooms": [], "raw": text[:1000]}

        if "rooms" not in data or not isinstance(data.get("rooms"), list):
            data["rooms"] = []

        rooms_sum = 0.0
        for r in data.get("rooms") or []:
            try:
                v = r.get("area_m2")
                if v is not None:
                    rooms_sum += float(v)
            except Exception:
                pass

        try:
            total = data.get("total_area_m2")
            if total is not None and rooms_sum > 0 and float(total) < rooms_sum:
                data["notes"] = list(data.get("notes") or [])
                data["notes"].append(
                    "total_area_m2 был меньше суммы помещений; заменён на сумму помещений"
                )
                data["total_area_m2"] = round(rooms_sum, 1)
        except Exception:
            pass

        return data

    except Exception as e:
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "rooms": [],
        }