import os
import numpy as np

logs = r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\logs"
emb_path = r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\embeddings.npy"
out_path = r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\profiles\a_profile.npy"

emb = np.load(emb_path, allow_pickle=True).item()

ok_vecs = []
seen_ok = 0
bad_lines = 0
missing_emb = 0

for fn in os.listdir(logs):
    if fn.startswith("a_feedback_") and fn.endswith(".tsv"):
        with open(os.path.join(logs, fn), encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("ts"):
                    continue
                parts = line.split("\t")
                if len(parts) < 4:
                    bad_lines += 1
                    continue
                label = parts[1].strip().upper()
                if label != "OK":
                    continue
                seen_ok += 1
                p = parts[3].strip()
                v = emb.get(p)
                if v is None:
                    missing_emb += 1
                    continue
                ok_vecs.append(v)

print("OK lines:", seen_ok, "OK vectors:", len(ok_vecs), "missing_emb:", missing_emb, "bad_lines:", bad_lines)
if len(ok_vecs) < 5:
    print("❌ too few OK vectors, need >= 5")
    raise SystemExit(1)

profile = np.mean(ok_vecs, axis=0).astype(np.float32)
os.makedirs(os.path.dirname(out_path), exist_ok=True)
np.save(out_path, profile)
print("✅ PROFILE SAVED:", out_path)
