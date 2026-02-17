import os, json, re
from pathlib import Path

RANK = Path("out/reports/a_rank_video_24h_audio.jsonl")
FEEDBACK = Path("out/logs/a_video_feedback_master.tsv")
OUT = Path("out/reports/a_video_smart.jsonl")

SUPER_BOOST = float(os.environ.get("AV_SUPER_BOOST", "0.08"))

# 🚫 блок-лист дети/котики/собачки
BLOCK_RE = re.compile(
    os.environ.get(
        "A_BLOCK_RE",
        r"(child|kid|baby|дет|ребен|кот|cat|dog|собак|puppy|kitty|щен)"
    ),
    re.I
)

def load_last_labels():
    last = {}
    if not FEEDBACK.exists():
        return last
    for line in FEEDBACK.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
        ts,label,score,path = line.split("\t",3)
        last[path] = label
    return last

rows = []
for line in RANK.read_text(encoding="utf-8", errors="ignore").splitlines():
    rows.append(json.loads(line))

last = load_last_labels()

out = []
for r in rows:
    p = r["path"]

    # 🚫 блок по пути
    if BLOCK_RE.search(p):
        continue

    score = r["score"]
    label = last.get(p, "")

    if label == "BAN":
        continue
    if label == "NO":
        score *= 0.2
    if label == "SUPER":
        score += SUPER_BOOST

    r["score"] = score
    r["label"] = label
    out.append(r)

out.sort(key=lambda x: x["score"], reverse=True)

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"[AV_SMART] wrote={len(out)} -> {OUT}")
