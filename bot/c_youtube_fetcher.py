from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs

from openai import OpenAI


_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_YT_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}

DEFAULT_MODEL = os.getenv("C_OPENAI_MODEL", "gpt-5")
MAX_TRIES = int(os.getenv("C_OPENAI_TRIES", "3"))
SLEEP_BETWEEN_TRIES_SEC = float(os.getenv("C_OPENAI_RETRY_SLEEP", "1.0"))

COOLDOWN_DAYS = int(os.getenv("C_SOURCE_COOLDOWN_DAYS", "7"))  # требование: 7
MAX_PER_SOURCE_PER_BATCH = 1  # требование: не больше 1 из одного источника за раз


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


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

        # accept only /watch
        if pu.path.rstrip("/") != "/watch":
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


def _norm_source_key(s: str) -> str:
    # источник = канал/автор (любой стабильный идентификатор, который вернёт модель)
    # нормализуем: lower, без лишних пробелов
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _cooldown_ok(source_key: str, last_sent_by_source: Dict[str, str], now: datetime) -> bool:
    if not source_key:
        # если источник не указан — НЕ пускаем (иначе опять будет "липнуть" и не сможем контролировать)
        return False

    last_iso = last_sent_by_source.get(source_key)
    if not last_iso:
        return True

    dt = _parse_iso(last_iso)
    if not dt:
        return True

    return (now - dt) >= timedelta(days=COOLDOWN_DAYS)


def _build_prompt(limit: int, banned_video_ids: Set[str], cooldown_days: int) -> str:
    banned = ""
    if banned_video_ids:
        sample = list(banned_video_ids)[:200]
        banned = "\n\nНЕ ИСПОЛЬЗУЙ эти video_id (уже отправлялись):\n" + "\n".join(sample)

    # ВАЖНО: просим ВОЗВРАЩАТЬ JSON.
    # И просим source_key = имя/handle/канал (любая строка-идентификатор источника).
    return (
        f"Найди {limit} РАЗНЫХ YouTube-ссылок на музыкальные КАВЕРЫ в стиле рок/метал.\n"
        f"Нужно смешать: часть каверов на иностранные известные песни, часть — на российские известные песни.\n\n"
        f"Требования:\n"
        f"- Только каверы (cover/кавер).\n"
        f"- Только YouTube watch URL формата https://www.youtube.com/watch?v=VIDEO_ID\n"
        f"- Не повторяй видео внутри ответа.\n"
        f"- В ответе ВЕРНИ ТОЛЬКО JSON-массив объектов, без текста вокруг.\n"
        f"- Каждый объект: {{\"url\": \"...\", \"title\": \"...\", \"source\": \"...\"}}\n"
        f"- Поле source: стабильный идентификатор источника (канал/автор/handle), строка.\n"
        f"- Не используй один и тот же source больше 1 раза в массиве.\n"
        f"- Учитывай правило: один source не должен повторяться чаще, чем раз в {cooldown_days} дней (подбирай разнообразные каналы).\n"
        f"{banned}"
    )


def _call_openai_json(limit: int, posted_video_ids: Set[str], cooldown_days: int) -> List[dict]:
    client = OpenAI()
    prompt = _build_prompt(limit, posted_video_ids, cooldown_days)

    resp = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    text = (getattr(resp, "output_text", "") or "").strip()
    if not text:
        return []

    # иногда модель может прислать ссылки без json — как fallback вытащим urls
    if not text.lstrip().startswith("["):
        urls = _extract_urls(text)
        out = []
        for u in urls:
            nu = _normalize_youtube_url(u)
            if nu:
                out.append({"url": nu, "title": "", "source": ""})
        return out

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass

    return []


def get_batch(
    *,
    limit: int,
    posted_video_ids: Set[str],
    last_sent_by_source: Dict[str, str],
) -> List[Dict[str, str]]:
    """
    Возвращает items для main.py:
      {
        "feed":"c_youtube",
        "item_id":video_id,
        "video_id":...,
        "url":...,
        "title":...,
        "source":...,
        "src":"openai_web_search"
      }
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    now = _now_utc()

    out: List[Dict[str, str]] = []
    seen_vid: Set[str] = set()
    used_sources: Set[str] = set()

    last_err: Optional[Exception] = None

    for _attempt in range(1, MAX_TRIES + 1):
        try:
            candidates = _call_openai_json(limit=limit * 3, posted_video_ids=posted_video_ids, cooldown_days=COOLDOWN_DAYS)
        except Exception as e:
            last_err = e
            time.sleep(SLEEP_BETWEEN_TRIES_SEC)
            continue

        for cand in candidates:
            url_raw = str(cand.get("url") or "").strip()
            title = str(cand.get("title") or "").strip()
            source = _norm_source_key(str(cand.get("source") or "").strip())

            nu = _normalize_youtube_url(url_raw)
            if not nu:
                continue

            vid = _video_id_from_url(nu)
            if not vid:
                continue

            if vid in posted_video_ids or vid in seen_vid:
                continue

            # 1) в одном батче не больше одного из источника
            if source in used_sources:
                continue

            # 2) cooldown 7 дней
            if not _cooldown_ok(source, last_sent_by_source, now):
                continue

            # 3) источник обязателен (иначе не контролируем повторяемость)
            if not source:
                continue

            seen_vid.add(vid)
            used_sources.add(source)

            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": nu,
                    "title": title,
                    "source": source,
                    "src": "openai_web_search",
                }
            )

            if len(out) >= limit:
                return out

        time.sleep(SLEEP_BETWEEN_TRIES_SEC)

    return out
