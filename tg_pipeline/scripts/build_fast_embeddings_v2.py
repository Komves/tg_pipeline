import os, subprocess, hashlib, numpy as np

root = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
mix = os.path.join(root, "data", "tg", "raw", "MIX")
out_dir = os.path.join(root, "out")
out_path = os.path.join(out_dir, "embeddings.npy")
os.makedirs(out_dir, exist_ok=True)

def md5vec(path):
    try:
        out = subprocess.check_output(
            ["ffmpeg","-loglevel","error","-t","1","-i",path,"-f","md5","-"],
            stderr=subprocess.STDOUT
        )
        h = hashlib.md5(out).digest()
        return np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 255.0
    except:
        return None

emb = {}
count = 0
for r,_,fs in os.walk(mix):
    for f in fs:
        if not f.lower().endswith(".mp4"):
            continue
        p = os.path.join(r,f)
        v = md5vec(p)
        count += 1
        if v is None:
            continue
        emb[p] = v
        if len(emb) % 200 == 0:
            print("saved:", len(emb), "scanned:", count)

np.save(out_path, emb)
print("DONE embeddings:", len(emb), "->", out_path)
