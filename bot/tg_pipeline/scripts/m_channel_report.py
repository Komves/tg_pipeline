import glob, os, pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS = os.path.join(ROOT, "out", "logs")

paths = sorted(glob.glob(os.path.join(LOGS, "m_feedback_*.tsv")))
if not paths:
    raise SystemExit("no m_feedback_*.tsv")

dfs = []
for p in paths:
    df = pd.read_csv(p, sep="\t")
    if "orig" not in df.columns:
        continue
    df = df[df["label"].isin(["OK","NO"])].copy()
    # channel = первая папка под MIX в orig
    # ...\data\tg\raw\MIX\<channel>\...
    def ch_from_orig(x):
        x = str(x)
        parts = x.replace("/", "\\").split("\\MIX\\", 1)
        if len(parts) < 2: return "UNKNOWN"
        tail = parts[1]
        return tail.split("\\", 1)[0] if "\\" in tail else tail
    df["channel"] = df["orig"].map(ch_from_orig)
    dfs.append(df)

m = pd.concat(dfs, ignore_index=True)
g = m.groupby(["channel","label"]).size().unstack(fill_value=0)
g["total"] = g.sum(axis=1)
g["ok_rate"] = (g.get("OK",0) / g["total"]).round(3)
g = g.sort_values(["ok_rate","total"], ascending=[False, False])

out = os.path.join(LOGS, "m_channel_okrate.tsv")
g.reset_index().to_csv(out, sep="\t", index=False)
print("WROTE", out)
print(g.reset_index().head(30).to_string(index=False))
