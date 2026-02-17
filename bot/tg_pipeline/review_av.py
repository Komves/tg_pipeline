import os, datetime

ROOT = r"out\tmp\av_review_local"
LOG  = r"out\logs\a_feedback_master.tsv"

files = [f for f in os.listdir(ROOT) if f.lower().endswith((".mp4",".mov",".m4v",".webm"))]
files.sort()

print("FOUND:", len(files))

for i, f in enumerate(files, 1):
    path = os.path.join(ROOT, f)
    os.startfile(os.path.abspath(path))

    ans = input(f"[{i}/{len(files)}] l/s/b/q > ").strip().lower()

    if ans == "q":
        break

    tag = {"l":"like","s":"skip","b":"ban"}.get(ans, "skip")

    with open(LOG, "a", encoding="utf-8") as w:
        w.write(f"A_VIDEO\t{datetime.date.today()}\t{path}\t{tag}\n")

print("done")
