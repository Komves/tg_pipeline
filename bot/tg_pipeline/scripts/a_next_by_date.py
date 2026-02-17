import os, json, random, shutil, pathlib, datetime, re

ROOT = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
MIX  = os.path.join(ROOT, "data", "tg", "raw", "MIX")
UI   = os.path.join(ROOT, "out", "tmp", "a_review_local")

SEEN_MASTER  = os.path.join(ROOT, "out", "logs", "a_seen.json")
SEEN_SESSION = os.path.join(ROOT, "out", "logs", "a_seen_session.json")

N = 70
MAX_DAYS = 5   # fallback: 1..5 days back

os.makedirs(UI, exist_ok=True)

def load_seen(path):
    if not os.path.exists(path):
        return set()
    try:
        arr = json.load(open(path, encoding="utf-8", errors="ignore"))
        return set(os.path.normpath(str(x)).lower() for x in arr)
    except:
        return set()

seen = load_seen(SEEN_MASTER) | load_seen(SEEN_SESSION)

today = datetime.date.today()
date_re = re.compile(r"(20\\d{2}-\\d{2}-\\d{2})")

def extract_date(path: str):
    m = date_re.search(path)
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except:
        return None

# build candidate pool with fallback window
cand = {}
used_days = None
for days in range(1, MAX_DAYS + 1):
    cut = today - datetime.timedelta(days=days)
    cand.clear()
    for p in pathlib.Path(MIX).rglob("*.mp4"):
        sp = str(p)
        d = extract_date(sp)
        if not d or d < cut:
            continue
        key = os.path.normpath(sp).lower()
        if key in seen:
            continue
        cand[key] = sp
    if len(cand) >= N or days == MAX_DAYS:
        used_days = days
        break

fresh = list(cand.values())
random.shuffle(fresh)
pick = fresh[:N]

# clear previous batch videos
for f in pathlib.Path(UI).glob("v_*.mp4"):
    try: f.unlink()
    except: pass

data=[]
for i,src in enumerate(pick,1):
    name=f"v_{i:03d}.mp4"
    shutil.copy2(src, os.path.join(UI,name))
    data.append({"idx":i,"score":0,"file":name,"src":src})

open(os.path.join(UI,"data.js"),"w",encoding="utf-8").write(
    "const data = "+json.dumps(data,ensure_ascii=False)+";"
)

# update session seen
sess = load_seen(SEEN_SESSION)
sess.update(os.path.normpath(x).lower() for x in pick)
open(SEEN_SESSION,"w",encoding="utf-8").write(json.dumps(sorted(sess),ensure_ascii=False))

print("PICK",len(pick),"POOL",len(fresh),"USED_DAYS",used_days)
