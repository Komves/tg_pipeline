from __future__ import annotations

import json
import math
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set, Dict


DATA_DIR = Path("/data")
RAW_DIR = DATA_DIR / "raw"
POSTED_TSV = DATA_DIR / "a_posted_master.tsv"

# только мемы (картинки)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

MAX_AGE_HOURS = 72.0
RECENCY_K = 2.0
RECENCY_TAU_H = 24.0

# анти-реклама: фильтр по caption/src
AD_KEYWORDS = {
    "казино", "casino", "ставк", "ставка", "бет", "bet", "bonus", "бонус",
    "промокод", "promo", "промо", "реферал", "реф", "рефк", "рефераль",
    "подпишись", "подписывайся", "переходи", "ссылка в", "в шапке", "по ссылке",
    "инвест", "инвестиции", "крипт", "crypto", "btc", "ton", "airdrop",
    "розыгрыш", "giveaway", "конкурс",
    "реклама", "advert", "ad:",
}
AD_SRC_HINTS = {
    "casino", "bet", "bonus", "promo", "airdrop", "crypto",
    "shop", "store", "market", "prod", "food", "fish",
    "alpenorth", "sale", "скидк", "акци", "ассортимент",
}

# diversity: максимум мемов с одного src за выдачу
MAX_PER_SRC = int(os.getenv("V_MEME_MAX_PER_SRC", "6"))

# дедуп по содержимому (берём первые 256KB + размер)
HASH_READ_BYTES = 256 * 1024


@dataclass(frozen=True)
class MemeItem:
    item_id: str
    abs_path: str
    score: float
    tg_date_iso: str
    src: str
    content_hash: str


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


def _iter_memes() -> Iterable[Path]:
    if not RAW_DIR.exists():
        return
    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".meta.json"):
            continue
        if p.suffix.lower() in IMAGE_EXT:
            yield p


def _meta_path(p: Path) -> Path:
    return Path(str(p) + ".meta.json")


def _read_meta(p: Path) -> Optional[dict]:
    mp = _meta_path(p)
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(p: Path, meta: dict) -> None:
    try:
        _meta_path(p).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_posted(user_id: int, feed: str) -> Set[str]:
    if not POSTED_TSV.exists():
        return set()
    out: Set[str] = set()
    for line in POSTED_TSV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        _ts, u, item, f = parts[0], parts[1], parts[2], parts[3]
        if str(u) == str(user_id) and f == feed:
            out.add(item)
    return out


def _recency_score(age_h: float) -> float:
    return RECENCY_K * math.exp(-age_h / RECENCY_TAU_H)


def _looks_like_ad(meta: dict) -> bool:
    cap = (meta.get("caption") or "")
    src = (meta.get("src") or "")
    s = (cap + " " + src).lower()

    for k in AD_KEYWORDS:
        if k in s:
            return True

    for k in AD_SRC_HINTS:
        if k in src.lower():
            return True

    return False


def _content_hash(p: Path, meta: dict) -> str:
    """
    Быстрый контент-хэш для дедупа: size + sha1(first 256KB).
    Кэшируем в meta.json как "content_hash".
    """
    ch = meta.get("content_hash")
    if isinstance(ch, str) and len(ch) >= 8:
        return ch

    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            head = f.read(HASH_READ_BYTES)
        h = hashlib.sha1()
        h.update(str(size).encode("utf-8"))
        h.update(b"|")
        h.update(head)
        ch = h.hexdigest()
    except Exception:
        # fallback — хоть что-то
        ch = f"err:{p.name}"

    meta["content_hash"] = ch
    _write_meta(p, meta)
    return ch


def rank_memes(user_id: int, n: int, feed: str = "feed_memes") -> List[MemeItem]:
    now = _now_utc()
    cut = now - timedelta(hours=MAX_AGE_HOURS)

    posted = _load_posted(user_id, feed)

    candidates: List[MemeItem] = []

    for p in _iter_memes():
        item_id = p.relative_to(RAW_DIR).as_posix()
        if item_id in posted:
            continue

        meta = _read_meta(p)
        if not meta:
            continue

        if _looks_like_ad(meta):
            continue

        tg_dt = _parse_iso((meta.get("tg_date") or "").strip())
        if not tg_dt or tg_dt < cut:
            continue

        age_h = max(0.0, (now - tg_dt).total_seconds() / 3600.0)
        score = _recency_score(age_h)

        src = (meta.get("src") or "UNKNOWN")
        ch = _content_hash(p, meta)

        candidates.append(
            MemeItem(
                item_id=item_id,
                abs_path=str(p),
                score=float(score),
                tg_date_iso=tg_dt.isoformat(),
                src=src,
                content_hash=ch,
            )
        )

    # сначала отсортируем по score
    candidates.sort(key=lambda x: x.score, reverse=True)

    # затем применим дедуп по content_hash и diversity по src
    out: List[MemeItem] = []
    seen_hash: Set[str] = set()
    per_src: Dict[str, int] = {}

    for it in candidates:
        if it.content_hash in seen_hash:
            continue
        cnt = per_src.get(it.src, 0)
        if cnt >= MAX_PER_SRC:
            continue

        out.append(it)
        seen_hash.add(it.content_hash)
        per_src[it.src] = cnt + 1

        if len(out) >= n:
            break

    return out
