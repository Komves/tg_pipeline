import os
import requests


NSFW_API_URL = os.getenv("NSFW_API_URL", "").strip()
NSFW_API_KEY = os.getenv("NSFW_API_KEY", "").strip()


def score_image(path: str) -> float:
    """
    Calls external NSFW API. Must return float 0..1 (higher = more NSFW).
    Expected API response JSON: {"score": 0.0..1.0}
    """
    if not NSFW_API_URL:
        raise RuntimeError("NSFW_API_URL is not set")
    if not NSFW_API_KEY:
        raise RuntimeError("NSFW_API_KEY is not set")

    with open(path, "rb") as f:
        files = {"image": (os.path.basename(path), f, "image/jpeg")}
        headers = {"Authorization": f"Bearer {NSFW_API_KEY}"}
        r = requests.post(NSFW_API_URL, files=files, headers=headers, timeout=30)

    r.raise_for_status()
    j = r.json()

    score = j.get("score", None)
    if score is None:
        raise RuntimeError(f"NSFW API bad response: {j}")

    return float(score)
