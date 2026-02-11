from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

from openai import OpenAI

# =========================
# CONFIG
# =========================
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_YT_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}

DEFAULT_MODEL = os.getenv("C_OPENAI_MODEL", "gpt-5")
MAX_TRIES = int(os.getenv("C_OPENAI_TRIES", "3"))
SLEEP_BETWEEN_TRIES_SEC = float(os.getenv("C_OPENAI_RETRY_SLEEP", "1.0"))

COOLDOWN_DAYS = int(os.getenv("C_SOURCE_COOLDOWN_DAYS", "7"))
MAX_PER_SOURCE_PER_BATCH = 1

# IMPORTANT: avoid hangs (set env C_OPENAI_TIMEOUT_SEC=60 to reduce timeouts)
OPENAI_TIMEOUT_SEC = float(os.getenv("C_OPENAI_TIMEOUT_SEC", "20"))

# Debug: print drop reasons and raw model output excerpt
C_DEBUG = (os.getenv("C_DEBUG", "0") or "").strip().lower() in {"1", "true", "yes", "on"}


def _cdbg(msg: str) -> None:
    if C_DEBUG:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[c_youtube] {now} {msg}", flush=True)


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
    """
    Normalize YouTube URL to canonical watch form:
      https://www.youtube.com/watch?v=VIDEO_ID

    NOTE: intentionally strict (only watch / youtu.be).
    If you later want shorts/playlists support, we can extend safely.
    """
    try:
        u = (u or "").strip().strip(").,;\"'")
        if not u:
            return None

        pu = urlparse(u)

        # youtu.be/<id>
        if pu.hostname in {"youtu.be"}:
            vid = pu.path.strip("/").split("/")[0]
            if not vid:
                return None
            return f"https://www.youtube.com/watch?v={vid}"

        if pu.hostname not in _YT_HOSTS:
            return None

        # accept /watch only
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
        f"- Учитывай правило: один source не должен повторяться чаще, чем раз в {cooldown_days} дней.\n"
        f"{banned}"
    )


def _extract_text(resp: Any) -> str:
    """
    Robustly extract text from OpenAI Responses API result.
    Some responses (esp. with tools) may not populate output_text.
    """
    for attr in ("output_text", "text"):
        if hasattr(resp, attr):
            val = getattr(resp, attr)
            if isinstance(val, str) and val.strip():
                return val.strip()

    out = getattr(resp, "output", None)
    if isinstance(out, list) and out:
        chunks: List[str] = []
        for item in out:
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for c in content:
                    txt = getattr(c, "text", None)
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt.strip())
        if chunks:
            return "\n".join(chunks).strip()

    return ""


def _call_openai_candidates(limit: int, posted_video_ids: Set[str], cooldown_days: int) -> List[dict]:
    client = OpenAI(timeout=OPENAI_TIMEOUT_SEC)
    prompt = _build_prompt(limit, posted_video_ids, cooldown_days)

    resp = client.responses.create(
        model=DEFAULT_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )

    text = (_extract_text(resp) or "").strip()
    if C_DEBUG:
        _cdbg(f"raw_out_len={len(text)} head={text[:180].replace(chr(10),' ')}")

    if not text:
        return []

    # 1) Prefer JSON
    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception as e:
            _cdbg(f"json_parse_failed: {type(e).__name__}: {e}")

    # 2) Fallback: plain URLs in text
    urls = _extract_urls(text)
    out: List[dict] = []
    for u in urls:
        nu = _normalize_youtube_url(u)
        if not nu:
            continue
        vid = _video_id_from_url(nu)
        if not vid:
            continue
        # IMPORTANT: provide non-empty source so it won't be dropped later
        out.append({"url": nu, "title": "", "source": f"yt:{vid}"})
    return out


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

    for attempt in range(1, MAX_TRIES + 1):
        try:
            candidates = _call_openai_candidates(
                limit=limit * 4,  # more candidates to survive filters
                posted_video_ids=posted_video_ids,
                cooldown_days=COOLDOWN_DAYS,
            )
        except Exception as e:
            _cdbg(f"openai_call_exc attempt={attempt}/{MAX_TRIES}: {type(e).__name__}: {e}")
            time.sleep(SLEEP_BETWEEN_TRIES_SEC)
            continue

        if C_DEBUG:
            _cdbg(f"attempt={attempt} candidates={len(candidates)}")

        for i, cand in enumerate(candidates):
            try:
                url_raw = str(cand.get("url") or "").strip()
                title = str(cand.get("title") or "").strip()
                source_raw = str(cand.get("source") or "").strip()
            except Exception:
                _cdbg(f"cand_bad_type idx={i}")
                continue

            nu = _normalize_youtube_url(url_raw)
            if not nu:
                _cdbg(f"drop normalize_fail idx={i} url={url_raw[:120]}")
                continue

            vid = _video_id_from_url(nu)
            if not vid:
                _cdbg(f"drop no_video_id idx={i} url={nu}")
                continue

            if vid in posted_video_ids:
                _cdbg(f"drop posted_dup idx={i} vid={vid}")
                continue

            if vid in seen_vid:
                _cdbg(f"drop batch_dup idx={i} vid={vid}")
                continue

            source = _norm_source_key(source_raw)
            if not source:
                source = f"yt:{vid}"
                _cdbg(f"source_missing -> fallback source={source} vid={vid}")

            # 1) not more than one per source per batch
            if source in used_sources:
                _cdbg(f"drop used_source idx={i} source={source}")
                continue

            # 2) cooldown
            if not _cooldown_ok(source, last_sent_by_source, now):
                _cdbg(f"drop cooldown idx={i} source={source}")
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

            _cdbg(f"keep idx={i} vid={vid} source={source} title_len={len(title)}")

            if len(out) >= limit:
                return out

        time.sleep(SLEEP_BETWEEN_TRIES_SEC)

    return out

