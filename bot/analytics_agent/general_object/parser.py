from __future__ import annotations

import re
from typing import Any, Dict


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_general_object_input(text: str) -> Dict[str, Any]:
    raw = compact(text)
    urls = re.findall(r"https?://\S+", raw)

    object_text = raw
    for url in urls:
        object_text = object_text.replace(url, " ")

    object_text = compact(object_text)

    return {
        "raw_text": raw,
        "object_text": object_text,
        "urls": urls,
        "input_type": "url" if urls else "text",
    }


def merge_current_object(current: Dict[str, Any] | None, incoming: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(current or {})
    incoming = dict(incoming or {})

    raw_text = compact(incoming.get("raw_text") or "")
    object_text = compact(incoming.get("object_text") or "")

    if raw_text:
        current["raw_text"] = raw_text

    if object_text:
        current["object_text"] = object_text
        current["display_name"] = object_text

    urls = list(current.get("urls") or [])
    for url in incoming.get("urls") or []:
        if url not in urls:
            urls.append(url)

    current["urls"] = urls
    current["input_type"] = incoming.get("input_type") or current.get("input_type") or "text"

    return current