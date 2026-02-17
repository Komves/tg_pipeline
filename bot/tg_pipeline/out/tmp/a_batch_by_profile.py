import os, json, numpy as np, re, random, subprocess, hashlib

mix=r"C:\Users\Марк\tg_pipeline\tg_pipeline\data\tg\raw\MIX"
ui=r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\tmp\a_review_local"
logs=r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\logs"
N=70
TOPK=1200

emb=np.load(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\embeddings.npy",allow_pickle=True).item()
p=np.load(r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\profiles\a_profile.npy")

def canon(path:str)->str:
    return os.path.normpath(path.strip()).lower()

def md5_1s(path:str)->str|None:
    try:
        out = subprocess.check_output(
            ["ffmpeg","-loglevel","error","-t","1","-i",path,"-f","md5","-"],
            stderr=subprocess.STDOUT
        )
        return hashlib.md5(out).hexdigest()
    except:
        return None

# seen by path + filename + md5 (из master)
seen_full=set()
seen_name=set()
seen_md5=set()

master=os.path.join(logs,"a_feedback_master.tsv")
with open(master,encoding="utf-8",errors="ignore") as f:
    for line in f:
        if ".mp4" in line.lower():
            parts=re.split(r"\s+", line.strip())
            if parts:
                path=parts[-1]
                c=canon(path)
                seen_full.add(c)
                seen_name.add(os.path.basename(c))
                h=md5_1s(path)
                if h: seen_md5.add(h)

def cos(a,b):
    return float(a@b)/(float(np.linalg.norm(a))*float(np.linalg.norm(b))+1e-12)

sc=[]
mixc=canon(mix)
for path,v in emb.items():
    cp=canon(path)
    if not cp.startswith(mixc):
        continue
    if cp in seen_full:
        continue
    if os.path.basename(cp) in seen_name:
        continue
    sc.append((cos(p,v), path))

sc.sort(reverse=True)
pool=[x[1] for x in sc[:min(TOPK,len(sc))]]
random.shuffle(pool)

pick=[]
used_md5=set(seen_md5)

for path in pool:
    if len(pick) >= N:
        break
    h=md5_1s(path)
    if not h:
        continue
    if h in used_md5:
        continue
    used_md5.add(h)
    pick.append(path)

# clear old
for fn in os.listdir(ui):
    if fn.startswith("v_") and fn.lower().endswith(".mp4"):
        os.remove(os.path.join(ui,fn))

import shutil
data=[]
for i,path in enumerate(pick,1):
    name=f"v_{i:03d}.mp4"
    shutil.copy2(path, os.path.join(ui,name))
    data.append({"idx":i,"score":0,"file":name,"src":path})

with open(os.path.join(ui,"data.js"),"w",encoding="utf-8") as f:
    f.write("const data = "+json.dumps(data,ensure_ascii=False)+";")

print("POOL",len(pool),"BATCH",len(pick),"SEEN_MD5",len(seen_md5))
