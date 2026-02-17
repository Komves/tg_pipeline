import os, time, json, re, shutil
from pathlib import Path

MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS","48"))
TARGET = int(os.environ.get("M_TARGET","70"))
CUT = time.time() - MAX_AGE_HOURS*3600

RAW = Path("data/tg/raw/MIX")
ROOT = Path("out/tmp/m_review_local")
DATA = ROOT/"data.js"
FEEDBACK = Path("out/logs/a_feedback_master.tsv")
BANNED_ITEM_FILE = Path("out/logs/a_meme_banned_items.txt")

def load_seen():
    s=set()
    if FEEDBACK.exists():
        for line in FEEDBACK.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            parts=line.split("\t",3)
            if len(parts)==4:
                s.add(parts[3].strip())
    return s

def load_list(p: Path):
    if not p.exists(): return set()
    return set(x.strip() for x in p.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip())

txt = DATA.read_text(encoding="utf-8", errors="ignore")
m_id = re.search(r"window\.BATCH_ID\s*=\s*([0-9]+);", txt)
m_it = re.search(r"window\.BATCH_ITEMS\s*=\s*(\[\{.*\}\]);", txt, flags=re.DOTALL)
if not (m_id and m_it):
    raise SystemExit("[M_FILL] cannot parse data.js")

batch_id = m_id.group(1)
items = json.loads(m_it.group(1))

seen = load_seen()
banned_item = load_list(BANNED_ITEM_FILE)

# what already in batch
orig_in = set((it.get("orig","") or "").strip() for it in items)
orig_in = set(x for x in orig_in if x)

# find fresh raw jpg
fresh = []
for p in RAW.rglob("*.jpg"):
    try:
        if p.stat().st_mtime < CUT:
            continue
    except:
        continue
    op = str(p.resolve())
    if op in seen: 
        continue
    if op in banned_item:
        continue
    if op in orig_in:
        continue
    fresh.append(p)

fresh.sort(key=lambda x: x.stat().st_mtime, reverse=True)

need = max(0, TARGET - len(items))
if need == 0:
    print(f"[M_FILL] ok already {len(items)}"); raise SystemExit(0)

added = 0
start_idx = 1
# find next m_### name
existing = sorted(ROOT.glob("m_*.jpg"))
if existing:
    nums=[]
    for e in existing:
        m=re.search(r"m_(\d+)\.jpg$", e.name)
        if m: nums.append(int(m.group(1)))
    if nums: start_idx = max(nums)+1

for p in fresh[:need]:
    added += 1
    name = f"m_{start_idx:03d}.jpg"
    start_idx += 1
    dst = ROOT/name
    shutil.copy2(p, dst)
    items.append({"src": name, "orig": str(p.resolve()), "label": ""})

out = f"window.BATCH_ID = {batch_id};\n"
out += "window.BATCH_ITEMS = " + json.dumps(items, ensure_ascii=False) + ";"
DATA.write_text(out, encoding="utf-8")

print(f"[M_FILL] added={added} total_now={len(items)} (target={TARGET}) max_age_h={MAX_AGE_HOURS}")
