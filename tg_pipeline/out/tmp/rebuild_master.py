import os, glob, re

logs = r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\logs"
master = os.path.join(logs, "a_feedback_master.tsv")

files = [f for f in glob.glob(os.path.join(logs, "a_feedback_*.tsv")) if os.path.getsize(f) >= 5000]
files = sorted(files)

rows = {}
for fn in files:
    with open(fn, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.lower().startswith("ts"):
                continue
            if not re.search(r"(^|\s)(OK|NO)(\s|$)", line):
                continue
            parts = re.split(r"\s+", line.strip())
            if len(parts) >= 4:
                path = parts[-1]
                rows[path] = line.strip()

out = ["ts\tlabel\tscore\tpath"] + list(rows.values())
with open(master, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

ok = sum(1 for v in rows.values() if re.search(r"(^|\s)OK(\s|$)", v))
no = sum(1 for v in rows.values() if re.search(r"(^|\s)NO(\s|$)", v))

print("FILES_USED", len(files), "ROWS", len(rows), "OK", ok, "NO", no)
