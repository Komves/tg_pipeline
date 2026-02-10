from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse, parse_qs

from openai import OpenAI

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_YT_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}

DEFAULT_MODEL = os.getenv("C_OPENAI_MODEL", "gpt-5")

# ВАЖНО: успеть до внешнего таймаута main (у тебя 20s)
# Поэтому по умолчанию держим короткий сетевой таймаут и мало попыток.
OPENAI_TIMEOUT_SEC = float(os.getenv("C_OPENAI_TIMEOUT_SEC", "8.0"))  # <= 20s budget
MAX_TRIES = int(os.getenv("C_OPENAI_TRIES", "2"))
SLEEP_BETWEEN_TRIES_SEC = float(os.getenv("C_OPENAI_RETRY_SLEEP", "0.6"))

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
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _cooldown_ok(source_key: str, last_sent_by_source: Dict[str, str], now: datetime) -> bool:
    if not source_key:
        return True  # если не смогли определить источник — не валим результат, иначе будет 0
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

    # Ключевая правка: явно просим RU+EN и даём модельке ключевые слова.
    # Просим source как handle/канал (но если не сможет — мы не будем выкидывать).
    return (
        f"Найди {limit} РАЗНЫХ YouTube-ссылок на музыкальные КАВЕРЫ (rock/metal).\n"
        f"Нужно смешать языки: примерно 50/50 RU и EN.\n\n"
        f"Подсказки для поиска:\n"
        f"- RU запросы/слова: кавер, каверы, перепевка, рок кавер, метал кавер, cover на русском, русская песня кавер\n"
        f"- EN запросы/слова: cover, rock cover, metal cover, live cover\n\n"
        f"Требования:\n"
        f"- Только каверы (cover/кавер/перепевка).\n"
        f"- Только YouTube watch URL: https://www.youtube.com/watch?v=VIDEO_ID (без плейлистов).\n"
        f"- Не повторяй видео внутри ответа.\n"
        f"- Верни ТОЛЬКО JSON-массив объектов, без текста вокруг.\n"
        f"- Формат объекта: {{\"url\":\"...\",\"title\":\"...\",\"source\":\"...\"}}\n"
        f"- source: имя/handle/канал/автор (любой стабильный идентификатор). Желательно.\n"
        f"- В одном ответе не больше 1 видео на один source.\n"
        f"- Старайся выбирать разные source, не повторяй source чаще, чем раз в {cooldown_days} дней.\n"
        f"{banned}"
    )


def _call_openai_json(limit: int, posted_video_ids: Set[str], cooldown_days: int) -> List[dict]:
    client = OpenAI(timeout=OPENAI_TIMEOUT_SEC)
    prompt = _build_prompt(limit, posted_video_ids, cooldown_days)

    resp = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    text = (getattr(resp, "output_text", "") or "").strip()
    if not text:
        return []

    # fallback: если пришёл не JSON — вытащим ссылки
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

    # чтобы не раздувать ответ и не тормозить — берём 2x, не 3x
    ask_n = min(20, max(limit * 2, limit))

    for _attempt in range(1, MAX_TRIES + 1):
        try:
            candidates = _call_openai_json(limit=ask_n, posted_video_ids=posted_video_ids, cooldown_days=COOLDOWN_DAYS)
        except Exception:
            time.sleep(SLEEP_BETWEEN_TRIES_SEC)
            continue

        for cand in candidates:
            url_raw = str(cand.get("url") or "").strip()
            title = str(cand.get("title") or "").strip()
            source_raw = str(cand.get("source") or "").strip()
            source = _norm_source_key(source_raw)

            nu = _normalize_youtube_url(url_raw)
            if not nu:
                continue

            vid = _video_id_from_url(nu)
            if not vid:
                continue

            if vid in posted_video_ids or vid in seen_vid:
                continue

            # если source пустой — не выбрасываем, делаем мягкий фоллбек
            # (иначе получишь 0 из-за "source обязателен")
            if not source:
                source = f"unknown-{vid[:6]}"

            # 1) в одном батче не больше одного из источника
            if source in used_sources:
                continue

            # 2) cooldown 7 дней
            if not _cooldown_ok(source, last_sent_by_source, now):
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
