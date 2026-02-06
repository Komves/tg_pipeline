import pathlib, datetime, re, os, json
ROOT = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
MIX = os.path.join(ROOT,"data","tg","raw","MIX")
SEENF = os.path.join(ROOT,"out","logs","a_seen_session.json")

seen=set()
if os.path.exists(SEENF):
    seen=set(os.path.normpath(x).lower() for x in json.load(open(SEENF,encoding="utf-8",errors="ignore")))

date_re=re.compile(r"(20\d{2}-\d{2}-\d{2})")
today=datetime.date.today()

stat={}
for p in pathlib.Path(MIX).rglob("*.mp4"):
    m=date_re.search(str(p))
    if not m: 
        continue
    d=datetime.date.fromisoformat(m.group(1))
    days=(today-d).days
    if days>5: 
        continue
    stat.setdefault(days,[0,0])  # total, not_seen
    stat[days][0]+=1
    if os.path.normpath(str(p)).lower() not in seen:
        stat[days][1]+=1

for d in sorted(stat):
    print(f"{d} days ago: total={stat[d][0]} not_seen={stat[d][1]}")
