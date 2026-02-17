import os, re, json, glob, shutil, random, subprocess, hashlib, argparse
from datetime import datetime, timedelta

def normpath(p):
    p = p.replace("\\\\", "\\")
    if p.startswith(":\\"):
        p = "C" + p
    return os.path.normpath(p)

def load_lines(path):
    if not os.path.exists(path):
        return []
    return [l.strip() for l in open(path, "r", encoding="utf-8", errors="ignore") if l.strip()]

def write_text(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(s)

def channel_from_path(p):
    m = re.search(r'[/\\]MIX[/\\]([^/\\]+)[/\\]', p, flags=re.I)
    return m.group(1) if m else None

def md5_1s(path):
    try:
        out = subprocess.check_output(
            ["ffmpeg","-loglevel","error","-t","1","-i",path,"-f","md5","-"],
            stderr=subprocess.STDOUT
        )
        return hashlib.md5(out).hexdigest()
    except:
        return None

def build_next_batch(root, N, exploit_n, explore_n):
    mix   = os.path.join(root, "data","tg","raw","MIX")
    ui    = os.path.join(root, "out","tmp","a_review_local")
    logs  = os.path.join(root, "out","logs")
    master= os.path.join(logs, "a_feedback_master.tsv")
    wl_p  = os.path.join(root, "out","config","a_channels_whitelist.txt")


    cutoff = datetime.now() - timedelta(hours=24)
    WH = set(load_lines(wl_p))

    seen_full=set()
    seen_name=set()
    if os.path.exists(master):
        for l in load_lines(master)[1:]:
            parts = l.split("\t")
            if len(parts)<4:
                continue
            p = normpath(parts[-1])
            seen_full.add(p.lower())
            seen_name.add(os.path.basename(p.lower()))

    def unseen(channel):
        arr=[]
        d=os.path.join(mix,channel)
        if not os.path.isdir(d):
            return arr
        for r,_,fs in os.walk(d):
            for fn in fs:
                if fn.lower().endswith(".mp4"):
                    p=normpath(os.path.join(r,fn))
                    try:
                        if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                            continue
                    except:
                        continue
                    if p.lower() in seen_full: continue
                    if os.path.basename(p.lower()) in seen_name: continue
                    arr.append(p)
        random.shuffle(arr)
        return arr

    exploit=[]
    for c in WH:
        exploit += unseen(c)
    random.shuffle(exploit)

    pick=[]
    used=set()

    def add_from(lst, need):
        nonlocal pick, used
        for p in lst:
            if len(pick)>=need:
                break
            h=md5_1s(p)
            if not h or h in used:
                continue
            used.add(h)
            pick.append(p)

    exp_target=min(exploit_n,N)
    add_from(exploit, exp_target)

    if len(pick)<N:
        add_from(exploit, N)

    os.makedirs(ui, exist_ok=True)

    for fn in os.listdir(ui):
        if fn.startswith("v_") and fn.lower().endswith(".mp4"):
            try: os.remove(os.path.join(ui,fn))
            except: pass

    data=[]
    for i,p in enumerate(pick,1):
        name=f"v_{i:03d}.mp4"
        shutil.copy(p, os.path.join(ui,name))
        data.append({"idx":i,"score":0,"file":name,"src":p})

    write_text(os.path.join(ui,"data.js"), "const data = "+json.dumps(data,ensure_ascii=False)+";")

    print("EXPLOIT_PICK", len(pick), "TOTAL_PICK", len(pick), "WH_CH", len(WH))

def fix_feedback(src, fixed):
    raw=open(src,"r",encoding="utf-8",errors="ignore").read().replace("\r","").replace("\\n","\n")
    lines=[l for l in raw.splitlines() if l.strip()]
    if not lines:
        write_text(fixed,"")
        return 0,0,0
    out=[lines[0]]
    ok=no=0
    for l in lines[1:]:
        l=re.sub(r"[ ]{2,}","\t",l.strip())
        l=re.sub(r"\t0([A-Za-z]:\\)",r"\t0\t\1",l)
        out.append(l)
        if re.search(r'(^|\s)OK(\s|$)',l): ok+=1
        elif re.search(r'(^|\s)NO(\s|$)',l): no+=1
    write_text(fixed,"\n".join(out)+"\n")
    return len(out)-1,ok,no

def rebuild_master(logs, master):
    files=sorted(glob.glob(os.path.join(logs,"a_feedback_*.tsv")))
    rows={}
    for fn in files:
        lines=load_lines(fn)
        for l in lines[1:]:
            parts=l.split("\t")
            if len(parts)<4: continue
            p=normpath(parts[-1])
            rows[p.lower()]="\t".join(parts[:3]+[p])
    out=["ts\tlabel\tscore\tpath"]+list(rows.values())
    write_text(master,"\n".join(out)+"\n")
    ok=sum(1 for v in rows.values() if "\tOK\t" in v)
    no=sum(1 for v in rows.values() if "\tNO\t" in v)
    return len(files),len(rows),ok,no

def cmd_save(root):
    logs=os.path.join(root,"out","logs")
    os.makedirs(logs,exist_ok=True)
    src=os.path.join(os.environ["USERPROFILE"],"Downloads","a_feedback.tsv")
    fixed=os.path.join(os.environ["USERPROFILE"],"Downloads","a_feedback_fixed.tsv")
    if not os.path.exists(src):
        print("NO_DOWNLOAD")
        return
    lines,ok,no=fix_feedback(src,fixed)
    print("FIXED_LINES",lines,"OK",ok,"NO",no)
    n=len(glob.glob(os.path.join(logs,"a_feedback_*.tsv")))+1
    dst=os.path.join(logs,f"a_feedback_{n:03d}.tsv")
    shutil.copy2(fixed,dst)
    files,rows,mok,mno=rebuild_master(logs,os.path.join(logs,"a_feedback_master.tsv"))
    print("MASTER OK",mok,"NO",mno,"TOTAL",mok+mno)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=r"C:\Users\Марк\tg_pipeline\tg_pipeline")
    sub=ap.add_subparsers(dest="cmd",required=True)
    s1=sub.add_parser("save")
    s2=sub.add_parser("next")
    s2.add_argument("--n",type=int,default=70)
    s2.add_argument("--exploit",type=int,default=70)
    s2.add_argument("--explore",type=int,default=0)
    args=ap.parse_args()
    if args.cmd=="save":
        cmd_save(args.root)
    if args.cmd=="next":
        build_next_batch(args.root,args.n,args.exploit,args.explore)

if __name__=="__main__":
    main()
