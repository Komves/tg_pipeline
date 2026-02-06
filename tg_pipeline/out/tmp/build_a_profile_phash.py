import os,re,numpy as np

root=r"C:\Users\Марк\tg_pipeline\tg_pipeline"
master=os.path.join(root,"out","logs","a_feedback_master.tsv")
emb=np.load(os.path.join(root,"out","phash_embeddings.npy"),allow_pickle=True).item()
out=os.path.join(root,"out","profiles","a_profile.npy")
os.makedirs(os.path.dirname(out),exist_ok=True)

ok=[]; no=[]
for line in open(master,encoding="utf-8",errors="ignore"):
    if ".mp4" not in line.lower(): 
        continue
    parts=re.split(r"\s+", line.strip())
    if len(parts)<4: 
        continue
    p=parts[-1].replace("\\\\","\\")
    if p.startswith(":\\"): p="C"+p
    v=emb.get(p)
    if v is None: 
        continue
    if re.search(r"(^|\s)OK(\s|$)", line): ok.append(v)
    elif re.search(r"(^|\s)NO(\s|$)", line): no.append(v)

print("OK_vecs",len(ok),"NO_vecs",len(no))
if len(ok)<10:
    raise SystemExit("need >=10 OK for stable profile")

okm=np.mean(ok,axis=0)
nom=np.mean(no,axis=0) if len(no)>0 else 0
profile=okm - nom   # discriminative
np.save(out, profile)
print("SAVED",out)
