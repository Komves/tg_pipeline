from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Set, Optional
from urllib.parse import urlparse, parse_qs

from openai import OpenAI


_YT_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

DEFAULT_MODEL = os.getenv("C_OPENAI_MODEL", "gpt-5")  # можно переопределить env
MAX_TRIES = int(os.getenv("C_OPENAI_TRIES", "3"))
SLEEP_BETWEEN_TRIES_SEC = float(os.getenv("C_OPENAI_RETRY_SLEEP", "1.0"))


def _extract_urls(text: str) -> List[str]:
    if not text:
        return []
    return _URL_RE.findall(text)


def _normalize_youtube_url(u: str) -> Optional[str]:
    try:
        u = u.strip().strip(").,;\"'")
        if not u:
            return None

        # youtu.be/<id>
        if "youtu.be/" in u:
            pu = urlparse(u)
            if pu.hostname not in _YT_HOSTS:
                return None
            vid = pu.path.strip("/").split("/")[0]
            if not vid:
                return None
            return f"https://www.youtube.com/watch?v={vid}"

        pu = urlparse(u)
        if pu.hostname not in _YT_HOSTS:
            return None

        # only watch URLs
        if pu.path.rstrip("/") not in ("/watch",):
            return None

        qs = parse_qs(pu.query)
        vid = (qs.get("v") or [""])[0].strip()
        if not vid:
            return None

        return f"https://www.youtube.com/watch?v={vid}"
    except Exception:
        return None


def _video_id_from_url(u: str) -> Optional[str]:
    try:
        pu = urlparse(u)
        qs = parse_qs(pu.query)
        vid = (qs.get("v") or [""])[0].strip()
        return vid or None
    except Exception:
        return None


def _build_prompt(limit: int, banned_video_ids: Set[str]) -> str:
    # ВАЖНО: просим ТОЛЬКО каверы, рок/метал, часть русских/часть иностранных
    # И просим выводить только ссылки (по 1 в строке), чтобы легко парсить.
    banned = ""
    if banned_video_ids:
        # чтобы не раздуть промпт, ограничим
        sample = list(banned_video_ids)[:200]
        banned = "\n\nНЕ ИСПОЛЬЗУЙ эти video_id (уже отправлялись):\n" + "\n".join(sample)

    return (
        f"Найди {limit} разных YouTube-ссылок на музыкальные клипы-КАВЕРЫ в стиле рок/метал "
        f"(можно metal/rock cover). Нужны реальные видео. "
        f"Смешай: часть каверов на иностранные известные песни, часть — на российские известные песни.\n\n"
        f"ТРЕБОВАНИЯ:\n"
        f"- Только каверы (в названии/описании должно быть cover/кавер или очевидно что это кавер).\n"
        f"- Ссылки должны быть только на YouTube (формат https://www.youtube.com/watch?v=VIDEO_ID).\n"
        f"- Без повторов внутри ответа.\n"
        f"- В ответе выведи ТОЛЬКО ссылки, по одной в строке, без текста.\n"
        f"{banned}"
    )


def _call_openai_for_links(limit: int, posted_video_ids: Set[str]) -> List[str]:
    client = OpenAI()

    prompt = _build_prompt(limit, posted_video_ids)

    # Используем встроенный web_search tool в Responses API:
    # модель реально ищет и возвращает найденное.
    resp = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    text = getattr(resp, "output_text", "") or ""
    urls_raw = _extract_urls(text)

    out: List[str] = []
    seen_vid: Set[str] = set()

    for u in urls_raw:
        nu = _normalize_youtube_url(u)
        if not nu:
            continue
        vid = _video_id_from_url(nu)
        if not vid:
            continue
        if vid in seen_vid:
            continue
        if vid in posted_video_ids:
            continue
        seen_vid.add(vid)
        out.append(nu)
        if len(out) >= limit:
            break

    return out


def get_batch(*, limit: int, posted_video_ids: Set[str]) -> List[Dict[str, str]]:
    """
    Возвращает items для main.py:
      {"feed":"c_youtube","item_id":video_id,"video_id":...,"url":...,"title":...,"src":...}
    Title берём пустым (можно расширить потом), url — реальный.
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_TRIES + 1):
        try:
            urls = _call_openai_for_links(limit=limit, posted_video_ids=posted_video_ids)
            if urls:
                items: List[Dict[str, str]] = []
                for u in urls:
                    vid = _video_id_from_url(u)
                    if not vid:
                        continue
                    items.append(
                        {
                            "feed": "c_youtube",
                            "item_id": vid,
                            "video_id": vid,
                            "url": u,
                            "title": "",     # можно добавить позже (если надо)
                            "src": "openai_web_search",
                        }
                    )
                return items
        except Exception as e:
            last_err = e

        time.sleep(SLEEP_BETWEEN_TRIES_SEC)

    # не падаем — просто вернём пусто (main.py залогирует ranked c_youtube=0)
    return []
