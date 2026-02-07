import os
import time
from typing import Optional, Dict, Any

import requests


NSFW_API_KEY = os.getenv("NSFW_API_KEY", "").strip()
NSFW_API_HOST = os.getenv("NSFW_API_HOST", "nsfw3.p.rapidapi.com").strip()
NSFW_API_URL = os.getenv("NSFW_API_URL", "https://nsfw3.p.rapidapi.com/v1/results").strip()

# hard limits to avoid 429
MIN_INTERVAL_SEC = float(os.getenv("NSFW_MIN_INTERVAL_SEC", "1.2"))  # ~0.8 rps default
MAX_RETRIES = int(os.getenv("NSFW_MAX_RETRIES", "5"))
TIMEOUT_SEC = float(os.getenv("NSFW_TIMEOUT_SEC", "30"))

_last_call_ts = 0.0


def _sleep_rate_limit():
    global _last_call_ts
    now = time.time()
    dt = now - _last_call_ts
    if dt < MIN_INTERVAL_SEC:
        time.sleep(MIN_INTERVAL_SEC - dt)
    _last_call_ts = time.time()


def score_image(image_path: str) -> Optional[Dict[str, Any]]:
    """
    Returns JSON dict from API, or None if failed.
    IMPORTANT:
      - On 401: returns None immediately (bad key/host/url).
      - On 429/5xx: retries with backoff.
    """
    if not NSFW_API_KEY:
        print("[nsfw] NSFW_API_KEY is empty")
        return None

    headers = {
        "X-RapidAPI-Key": NSFW_API_KEY,
        "X-RapidAPI-Host": NSFW_API_HOST,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        _sleep_rate_limit()

        try:
            with open(image_path, "rb") as f:
                files = {"image": f}
                r = requests.post(NSFW_API_URL, headers=headers, files=files, timeout=TIMEOUT_SEC)
        except Exception as e:
            print(f"[nsfw] request error attempt={attempt}: {e}")
            time.sleep(1.5 * attempt)
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                print(f"[nsfw] bad json: {e}")
                return None

        if r.status_code == 401:
            print(f"[nsfw] 401 Unauthorized (check NSFW_API_KEY/HOST/URL). url={NSFW_API_URL}")
            return None

        if r.status_code == 429:
            wait = 2.0 * attempt
            print(f"[nsfw] 429 Too Many Requests, backoff {wait}s")
            time.sleep(wait)
            continue

        if 500 <= r.status_code < 600:
            wait = 2.0 * attempt
            print(f"[nsfw] {r.status_code} server error, backoff {wait}s")
            time.sleep(wait)
            continue

        print(f"[nsfw] HTTP {r.status_code}: {r.text[:200]}")
        return None

    return None
