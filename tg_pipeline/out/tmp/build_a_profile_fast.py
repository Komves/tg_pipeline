import os, re, numpy as np

master = os.path.join(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\logs", "a_feedback_master.tsv")
emb = np.load(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\embeddings.npy", allow_pickle=True).item()

ok=[]
with open(master, encoding="utf-8", errors="ignore") as f:
    for line in f:
        if re.search(r"(^|\s)OK(\s|$)", line):
            parts=re.split(r"\s+", line.strip())
            if len(parts)>=4:
                p=parts[-1]
                v=emb.get(p)
                if v is not None:
                    ok.append(v)

print("OK_vecs", len(ok))
if len(ok) < 5:
    raise SystemExit("need >=5 OK vectors")

profile = np.mean(ok, axis=0)
np.save(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\profiles\a_profile.npy", profile)
print("SAVED", r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\profiles\a_profile.npy")
