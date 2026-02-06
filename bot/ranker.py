import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass

DATA_DIR = Path("/data")
RAW_DIR = DATA_DIR / "raw"
POSTED = DATA_DIR / "a_posted_master.tsv"

CAT_A_VIDEO = "A_VIDEO"


@dataclass
class Item:
    item_id: str
    abs_path: Path
    tg_date: datetime
    score: float


# ---------- AUDIO DETECTION ----------
def has_audio(path: Path) -> bool:
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json",
            str(path)
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        j = json.loads(out)
        return len(j.get("streams", [])) > 0
    except:
        return False


# ---------- META ----------
def read_meta(path: Path):
    meta = Path(str(path) + ".meta.json")
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except:
        return None


def parse_dt(s):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None


# ---------- POSTED ----------
def load_posted():
    s = set()
    if not POSTED.exists():
        return s
    for line in POSTED.read_text(encoding="utf-8").splitlines():
        if line.startswith("timestamp"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            s.add(parts[2])
    return s


def save_posted(item_id):
    if not POSTED.exists():
        POSTED.write_text("timestamp\tuser\titem\tfeed\n", encoding="utf-8")

    with POSTED.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.utcnow().isoformat()}\t0\t{item_id}\tfeed_a_video\n")


# ---------- CORE ----------
def collect_candidates():

    items = []

    for p in RAW_DIR.rglob("*.mp4"):

        meta = read_meta(p)
        if not meta:
            continue

        dt = parse_dt(meta.get("tg_date",""))
        if not dt:
            continue

        # AUDIO FILTER
        if not has_audio(p):
            continue

        item_id = p.relative_to(RAW_DIR).as_posix()

        score = dt.timestamp()

        items.append(Item(
            item_id=item_id,
            abs_path=p,
            tg_date=dt,
            score=score
        ))

    return items


def rank_top_n(user_id, category, n, feed="feed_a_video"):

    items = collect_candidates()

    posted = load_posted()

    items = [i for i in items if i.item_id not in posted]

    items.sort(key=lambda x: x.score, reverse=True)

    top = items[:n]

    for i in top:
        save_posted(i.item_id)

    return top
