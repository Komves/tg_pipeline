import numpy as np

mix = r"C:\Users\Марк\tg_pipeline\tg_pipeline\data\tg\raw\MIX"
emb = np.load(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\embeddings.npy", allow_pickle=True).item()
profile = np.load(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\profiles\a_profile.npy")

def cos(a,b):
    return float(a @ b) / (float(np.linalg.norm(a)) * float(np.linalg.norm(b)) + 1e-12)

scored=[]
for p,v in emb.items():
    if p.startswith(mix):
        scored.append((cos(profile,v), p))

scored.sort(reverse=True)
print("TOP 20:")
for s,p in scored[:20]:
    print(round(s,4), p)
