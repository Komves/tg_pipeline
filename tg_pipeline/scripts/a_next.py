import os
import json
import random
import shutil
import pathlib
import re
import datetime
import time

ROOT = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
MIX  = ROOT + r"\data\tg\raw\MIX"
UI   = ROOT + r"\out\tmp\a_review_local"
WL   = ROOT + r"\out\config\a_channels_whitelist.txt"

TODAY = datetime.date.today().isoformat()
SEENF = ROOT + rf"\out\logs\a_seen_{TODAY}.json"

N = 70
CUT_DATE = datetime.date.today() - datetime.timedelta(days=1)

os.makedirs(UI, exist_ok=True)

# load whitelist
wh = set()
with open(WL, encoding="utf-8", errors="ignore") as f:
    for l in f:
        l = l.strip().lstrip("\ufeff")
        if l:
            wh.add(l)

def channel_from_path(p: str):
    m = re.search(r"[\\/]+MIX[\\/]+([^\\/]+)[\\/]+", p, flags=re.I)
    return m.group(1) if m else None

def date_from_path(p: str):
    parts = pathlib.Path(p).parts
    if len(parts) < 2:
        return None
    d = parts[-2]
    try:
        return datetime.date.fromisoformat(d)
    except:
        return None

def load_seen():
    if not os.path.exists(SEENF):
        return set()
    try:
        return set(x.lower() for x in json.load(open(SEENF, encoding="utf-8", errors="ignore")))
    except:
        return set()

seen = load_seen()

pool = {}
for p in pathlib.Path(MIX).rglob("*.mp4"):
    sp = str(p)

    ch = channel_from_path(sp)
    if not (ch and ch in wh):
        continue

    d = date_from_path(sp)
    if not d or d < CUT_DATE:
        continue

    key = os.path.normpath(sp).lower()
    if key in seen:
        continue

    pool[key] = sp

cand = list(pool.values())
random.shuffle(cand)
pick = cand[:N]

# clear old files
for f in pathlib.Path(UI).glob("v_*.mp4"):
    try:
        f.unlink()
    except:
        pass

batch_id = int(time.time())

data = []
for i, src in enumerate(pick, 1):
    name = f"v_{i:03d}_{batch_id}.mp4"
    shutil.copy2(src, UI + "\\" + name)
    data.append({"idx": i, "score": 0, "file": name, "src": src})

with open(UI + "\\data.js", "w", encoding="utf-8") as f:
    f.write("const data = " + json.dumps(data, ensure_ascii=False) + ";")

seen.update(os.path.normpath(x).lower() for x in pick)
with open(SEENF, "w", encoding="utf-8") as f:
    json.dump(sorted(seen), f, ensure_ascii=False)

print("A_NEXT_OK",
      "COPIED", len(pick),
      "WHITELIST", len(wh),
      "CUT_DATE", CUT_DATE.isoformat(),
      "BATCH", batch_id)
