import os,re,numpy as np
from sklearn.linear_model import LogisticRegression
import joblib

root=r"C:\Users\Марк\tg_pipeline\tg_pipeline"
master=os.path.join(root,"out","logs","a_feedback_master.tsv")
emb=np.load(os.path.join(root,"out","phash_embeddings.npy"),allow_pickle=True).item()
out=os.path.join(root,"out","models")
os.makedirs(out,exist_ok=True)

X=[]; y=[]
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
    if re.search(r"(^|\s)OK(\s|$)", line):
        X.append(v); y.append(1)
    elif re.search(r"(^|\s)NO(\s|$)", line):
        X.append(v); y.append(0)

X=np.array(X,dtype=np.float32)
y=np.array(y,dtype=np.int32)
print("TRAIN n=",len(y),"pos=",int(y.sum()),"neg=",int((1-y).sum()))

clf=LogisticRegression(max_iter=2000, class_weight="balanced")
clf.fit(X,y)

joblib.dump(clf, os.path.join(out,"a_lr_model.pkl"))
print("SAVED", os.path.join(out,"a_lr_model.pkl"))
