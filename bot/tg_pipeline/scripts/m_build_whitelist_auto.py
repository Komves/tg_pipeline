import os
import pandas as pd

ROOT = os.path.abspath(".")
rep  = os.path.join(ROOT, "out", "logs", "m_channel_okrate.tsv")
out  = os.path.join(ROOT, "out", "config", "m_channels_whitelist.txt")

TOP = 10
MIN_TOTAL = 10
MIN_OKRATE = 0.25

df = pd.read_csv(rep, sep="\t")
df = df[(df["total"] >= MIN_TOTAL) & (df["ok_rate"] >= MIN_OKRATE)].copy()
df = df.sort_values(["ok_rate", "total"], ascending=[False, False]).head(TOP)

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    for ch in df["channel"].tolist():
        f.write(ch + "\n")

print("WROTE:", out)
print(df[["channel","ok_rate","total"]].to_string(index=False))
