import glob, os
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS = os.path.join(ROOT, "out", "logs")

files = sorted(glob.glob(os.path.join(LOGS, "m_feedback_*.tsv")))
if not files:
    raise SystemExit("no m_feedback_*.tsv found")

dfs = []
for p in files:
    df = pd.read_csv(p, sep="\t")
    df["src_log"] = os.path.basename(p)
    dfs.append(df)

m = pd.concat(dfs, ignore_index=True)

# оставляем только OK/NO
m = m[m["label"].isin(["OK","NO"])]

# дедуп по path (последняя метка важнее)
m = m.drop_duplicates(subset=["path"], keep="last")

out = os.path.join(LOGS, "m_feedback_master.tsv")
m.to_csv(out, sep="\t", index=False)

ok = int((m["label"]=="OK").sum())
no = int((m["label"]=="NO").sum())
print("WROTE", out)
print("OK", ok, "NO", no, "TOTAL", len(m))
