import os, subprocess, numpy as np
from PIL import Image
import imagehash

root=r"C:\Users\Марк\tg_pipeline\tg_pipeline"
mix=os.path.join(root,"data","tg","raw","MIX")
out=os.path.join(root,"out","phash_embeddings.npy")
os.makedirs(os.path.dirname(out), exist_ok=True)

def phash_vec(video_path):
    # 3 кадра: 1s, 3s, 5s -> phash 64bit -> 64 floats
    vec=np.zeros(64, dtype=np.float32)
    got=0
    for ss in (1,3,5):
        try:
            cmd=["ffmpeg","-loglevel","error","-ss",str(ss),"-i",video_path,"-frames:v","1","-f","image2pipe","-vcodec","png","-"]
            png=subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            img=Image.open(__import__("io").BytesIO(png)).convert("RGB")
            h=imagehash.phash(img)  # 64-bit
            bits=np.array([1.0 if c=='1' else 0.0 for c in bin(int(str(h),16))[2:].zfill(64)], dtype=np.float32)
            vec += bits
            got += 1
        except:
            pass
    if got==0:
        return None
    vec /= got
    return vec

emb={}
n=0
for r,_,fs in os.walk(mix):
    for fn in fs:
        if fn.lower().endswith(".mp4"):
            p=os.path.join(r,fn)
            v=phash_vec(p)
            if v is not None:
                emb[p]=v
                n+=1
                if n%200==0:
                    print("emb:",n)
np.save(out, emb)
print("DONE",len(emb),"->",out)
