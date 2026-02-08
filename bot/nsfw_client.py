import os
import time
from typing import Optional, Dict, Any

import requests


NSFW_API_KEY = os.getenv("NSFW_API_KEY", "").strip()
NSFW_API_HOST = os.getenv("NSFW_API_HOST", "nsfw3.p.rapidapi.com").strip()
NSFW_API_URL = os.getenv("NSFW_API_URL", "https://nsfw3.p.rapidapi.com/v1/results").strip()

# hard limits to avoid 429 (rate-limit only; total-call limit is runner's job)
MIN_INTERVAL_SEC = float(os.getenv("NSFW_MIN_INTERVAL_SEC", "1.2"))  # ~0.8 rps default
TIMEOUT_SEC = float(os.getenv("NSFW_TIMEOUT_SEC", "30"))

# IMPORTANT: keep retries small here; runner must not get stuck
MAX_RETRIES = int(os.getenv("NSFW_MAX_RETRIES", "2"))  # was 5, but causes long loops

_last_call_ts = 0.0


class QuotaExhausted(Exception):
    """RapidAPI quota/subscription exhausted or blocked."""
    pass


class TemporaryNsfwError(Exception):
    """Temporary network/server errors."""
    pass


def _sleep_rate_limit():
    global _last_call_ts
    now = time.time()
    dt = now - _last_call_ts
    if dt < MIN_INTERVAL_SEC:
        time.sleep(MIN_INTERVAL_SEC - dt)
    _last_call_ts = time.time()


def _looks_like_quota(text: str) -> bool:
    t = (text or "").lower()
    # RapidAPI typical messages
    return (
        "used 100% of basic subscription" in t
        or "used 100% of" in t
        or "quota" in t and ("exceed" in t or "exceeded" in t or "limit" in t)
        or "not subscribed" in t
        or "subscription" in t and ("ended" in t or "expired" in t or "inactive" in t)
        or "too many requests" in t
    )


def score_image(image_path: str) -> Optional[Dict[str, Any]]:
    """
    Returns JSON dict from API, or None if failed.

    Behavior:
      - On missing key: returns None immediately.
      - On 401: returns None immediately (bad key/host/url).
      - On 429/403 quota/subscription: raises QuotaExhausted (NO infinite retry).
      - On 5xx / network: limited retries then returns None.
      - On other 4xx: returns None immediately.
    """
    if not NSFW_API_KEY:
        print("[nsfw] NSFW_API_KEY is empty")
        return None

    headers = {
        "X-RapidAPI-Key": NSFW_API_KEY,
        "X-RapidAPI-Host": NSFW_API_HOST,
    }

    last_err: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        _sleep_rate_limit()

        try:
            with open(image_path, "rb") as f:
                files = {"image": f}
                r = requests.post(
                    NSFW_API_URL,
                    headers=headers,
                    files=files,
                    timeout=TIMEOUT_SEC,
                )
        except Exception as e:
            last_err = f"request error attempt={attempt}: {e}"
            print(f"[nsfw] {last_err}")
            # short bounded backoff
            time.sleep(min(1.5 * attempt, 4.0))
            continue

        # success
        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                print(f"[nsfw] bad json: {e}")
                return None

        # auth/config error - do not retry
        if r.status_code == 401:
            print(f"[nsfw] 401 Unauthorized (check NSFW_API_KEY/HOST/URL). url={NSFW_API_URL}")
            return None

        # quota / throttling - STOP fast
        if r.status_code == 429:
            msg = (r.text or "")[:300]
            print(f"[nsfw] 429 Too Many Requests -> STOP scoring this run. body={msg}")
            raise QuotaExhausted("429 Too Many Requests")

        if r.status_code == 403:
            body = r.text or ""
            msg = body[:300]
            if _looks_like_quota(body):
                print(f"[nsfw] 403 quota/subscription -> STOP scoring this run. body={msg}")
                raise QuotaExhausted("403 quota/subscription")
            print(f"[nsfw] 403 Forbidden (non-quota). body={msg}")
            return None

        # server errors - bounded retries
        if 500 <= r.status_code < 600:
            wait = min(2.0 * attempt, 6.0)
            print(f"[nsfw] {r.status_code} server error, backoff {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue

        # other client errors - do not retry
        body = (r.text or "")[:300]
        if _looks_like_quota(body):
            print(f"[nsfw] HTTP {r.status_code} looks like quota/subscription -> STOP scoring. body={body}")
            raise QuotaExhausted(f"HTTP {r.status_code} quota-like response")

        print(f"[nsfw] HTTP {r.status_code}: {body}")
        return None

    # retries exhausted
    if last_err:
        print(f"[nsfw] failed after retries: {last_err}")
    return None
