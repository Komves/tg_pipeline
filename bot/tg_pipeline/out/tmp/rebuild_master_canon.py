import os, glob, re

logs=r"C:\Users\Марк\tg_pipeline\tg_pipeline\out\logs"
master=os.path.join(logs,"a_feedback_master.tsv")

def canon(p:str)->str:
    return os.path.normpath(p.strip()).lower()

files=sorted(glob.glob(os.path.join(logs,"a_feedback_*.tsv")))
rows={}
bad=0

for fn in files:
    with open(fn,encoding="utf-8",errors="ignore") as f:
        for line in f:
            if line.lower().startswith("ts"): 
                continue
            m=re.search(r"(^|\s)(OK|NO)(\s|$)", line)
            if not m:
                continue
            parts=re.split(r"\s+", line.strip())
            if len(parts) < 4:
                bad+=1
                continue
            ts = parts[0] + " " + parts[1] if re.match(r"\d{4}-\d{2}-\d{2}", parts[0]) else parts[0]
            # label обычно отдельным токеном где-то рядом; проще — возьмём первый OK/NO
            lab = "OK" if "OK" in parts else "NO"
            path = parts[-1]
            key = canon(path)
            rows[key] = (lab, path)

out=["ts\tlabel\tscore\tpath"]
ok=no=0
for key,(lab,path) in rows.items():
    if lab=="OK": ok+=1
    else: no+=1
    out.append(f"0\t{lab}\t0\t{path}")

with open(master,"w",encoding="utf-8") as f:
    f.write("\n".join(out)+"\n")

print("FILES_USED",len(files),"ROWS",len(rows),"OK",ok,"NO",no,"BAD",bad)
