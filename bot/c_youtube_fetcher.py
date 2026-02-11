# c_youtube_fetcher.py
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

from openai import OpenAI

MODEL = os.getenv("C_OPENAI_MODEL", "gpt-4o-mini")

# мало кандидатов, чтобы не грузить OpenAI
CANDIDATES_PER_CALL = int(os.getenv("C_YT_CANDIDATES_PER_CALL", "6"))
MAX_CALLS = int(os.getenv("C_YT_MAX_CALLS", "3"))

# проверки
OEMBED_TIMEOUT_SEC = float(os.getenv("C_YT_OEMBED_TIMEOUT_SEC", "6.0"))
# если не набрали живых — добиваем непроверенными (чтобы НЕ было returning=0)
ALLOW_UNVERIFIED_FALLBACK = (os.getenv("C_YT_ALLOW_UNVERIFIED_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"})

URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_\-]{11}")
VID_RE = re.compile(r"v=([A-Za-z0-9_\-]{11})")


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[c_youtube] {now} {msg}", flush=True)


def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    if u.startswith("https://youtube.com/"):
        u = "https://www." + u[len("https://") :]
    return u


def _extract_video_id(url: str) -> str:
    m = VID_RE.search(url or "")
    return m.group(1) if m else ""


def _oembed_check(url: str) -> Tuple[bool, str]:
    """
    Проверка "живости" через YouTube oEmbed.
    Возвращает (ok, title). Если oEmbed недоступен в среде — всё будет false.
    """
    url = _norm_url(url)
    if not url:
        return False, ""

    q = urllib.parse.urlencode({"url": url, "format": "json"})
    oembed = f"https://www.youtube.com/oembed?{q}"

    try:
        req = urllib.request.Request(
            oembed,
            method="GET",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=OEMBED_TIMEOUT_SEC) as r:
            code = int(getattr(r, "status", 0) or 0)
            raw = r.read()
            if code != 200:
                return False, ""
            data = json.loads(raw.decode("utf-8", errors="ignore") or "{}")
            title = (data.get("title") or "").strip()
            return True, title
    except Exception:
        return False, ""


def _openai_pick_urls(prompt: str) -> List[str]:
    client = OpenAI()
    resp = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    text = (resp.output_text or "").strip()
    urls = URL_RE.findall(text)
    # нормализуем и чистим
    out: List[str] = []
    seen = set()
    for u in urls:
        nu = _norm_url(u)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
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
        "feed": "c_youtube",
        "item_id": vid,
        "video_id": vid,
        "url": url,
        "title": title,
        "source": f"yt:{vid}",
        "ts": int(time.time())
      }

    Логика:
    - Просим немного кандидатов у OpenAI (через web_search)
    - Пробуем отфильтровать реально живые через oEmbed
    - Если живых не хватает и включён fallback — добиваем непроверенными
      (иначе ты будешь постоянно получать ranked c_youtube=0)
    """
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    out: List[Dict[str, str]] = []
    seen_vid: Set[str] = set()
    unverified_pool: List[Tuple[str, str]] = []  # (vid, url)

    themes = [
        "rock cover live session 2023 2024",
        "metal cover guitar vocal 2022 2023",
        "post-hardcore cover acoustic rock",
    ]

    for call_idx in range(MAX_CALLS):
        theme = themes[call_idx % len(themes)]

        prompt = (
            f"Найди {CANDIDATES_PER_CALL} РАЗНЫХ YouTube ссылок формата https://www.youtube.com/watch?v=... "
            f"по теме: {theme}. "
            f"Не shorts. Не плейлисты. Только watch?v=. "
            f"Верни только ссылки, по одной на строку."
        )

        urls = _openai_pick_urls(prompt)
        log(f"call={call_idx+1}/{MAX_CALLS} urls_found={len(urls)} theme='{theme}'")

        for url in urls:
            vid = _extract_video_id(url)
            if not vid:
                continue

            if vid in seen_vid:
                continue
            seen_vid.add(vid)

            if vid in posted_video_ids:
                continue

            # сохраняем кандидата в пул "на всякий"
            unverified_pool.append((vid, url))

            ok, title = _oembed_check(url)
            if not ok:
                log(f"skip dead youtube: {url}")
                continue

            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": url,
                    "title": title,
                    "source": f"yt:{vid}",
                    "ts": int(time.time()),
                }
            )

            if len(out) >= limit:
                log(f"returning={len(out)}")
                return out

        time.sleep(0.3)

    # === fallback: добиваем непроверенными, чтобы НЕ было 0 ===
    if ALLOW_UNVERIFIED_FALLBACK and len(out) < limit:
        need = limit - len(out)
        added = 0
        for vid, url in unverified_pool:
            if added >= need:
                break
            # не повторяем то, что уже добавили живыми
            if any(x.get("video_id") == vid for x in out):
                continue
            out.append(
                {
                    "feed": "c_youtube",
                    "item_id": vid,
                    "video_id": vid,
                    "url": url,
                    "title": "",  # неизвестно
                    "source": f"yt:{vid}",
                    "ts": int(time.time()),
                }
            )
            added += 1

        if added:
            log(f"fallback_unverified_added={added}")

    log(f"returning={len(out)}")
    return out
