import os, subprocess, hashlib, numpy as np

root = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
mix = os.path.join(root, "data", "tg", "raw", "MIX")
out_path = os.path.join(root, "out", "embeddings.npy")
os.makedirs(os.path.dirname(out_path), exist_ok=True)

def vec(path):
    try:
        # md5 первых ~1 сек (через ffmpeg) -> 16 байт -> 16 float32
        out = subprocess.check_output(["ffmpeg","-loglevel","error","-t","1","-i",path,"-f","md5","-"])
        h = hashlib.md5(out).digest()
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 255.0
        return v
    except:
        return None

emb = {}
count = 0

for r,_,fs in os.walk(mix):
    for f in fs:
        if not f.lower().endswith(".mp4"):
            continue
        p = os.path.join(r,f)
        v = vec(p)
        if v is None:
            continue
        emb[p] = v
        count += 1
        if count % 200 == 0:
            print("encoded:", count)

np.save(out_path, emb)
print("DONE embeddings:", len(emb), "->", out_path)
