import json, base64, random, subprocess, time
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parent

# --- tuning knobs ---
TOPN = 80                 # сколько покажем в review
POOL = 900                # сколько набираем кандидатов до сортировки
MAX_MB = 45               # отсечь слишком жирные (часто не мемы)
MIN_MB = 0.7              # отсечь мусор/обрубки
MAX_SEC = 35.0            # отсечь длинные (часто не мемы)
MIN_SEC = 2.0             # отсечь микроклипы
SEED = 42                 # воспроизводимость
RECENCY_WEIGHT = 0.92     # 0..1: 1 = почти чисто свежие, 0 = чисто рандом

# Явный whitelist (по твоему списку папок; можно расширять)
ALLOW_FOLDERS = {
    "IT_Humor_VIP",
    "memomagia",
    "rjaka_memi",
    "smeh_proletariya",
    "smesnonet",
    "i_taaak_soidet",
    "delusion_generator",
    "dvestroki",
    "Hoduttut",
    "gusgagarik",
    "NoviyDubll",
}

# fallback ключи, если whitelist пустой/не совпал
MEME_KEYS = ["humor", "meme", "mem", "rjaka", "smeh", "smes", "taaak", "soidet", "prikol", "lol"]

def load_cfg():
    return json.loads((ROOT / "out" / "config" / "engine.local.json").read_text(encoding="utf-8-sig"))

def ffprobe_dur(p: Path):
    try:
        r = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1", str(p)],
            capture_output=True, text=True, timeout=12
        )
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        return None

def build_html(b64: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review A (funny videos)</title>
<style>
body{{font-family:Arial;margin:16px}}.wrap{{max-width:980px;margin:0 auto}}
video{{width:100%;max-height:72vh;background:#000;border-radius:14px}}
button{{font-size:18px;padding:10px 16px;margin-right:10px;border-radius:12px;border:1px solid #bbb;cursor:pointer}}
button.ok{{border-color:#2b8a3e}}button.no{{border-color:#c92a2a}}
.small{{font-size:12px;color:#555;word-break:break-all;font-family:Consolas,monospace}}
.bar{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}}
kbd{{padding:2px 6px;border:1px solid #ccc;border-radius:6px;background:#f7f7f7}}
</style></head>
<body><div class="wrap">
<h2>A Review — прикольные видео (mp4) из мем-папок</h2>
<div class="bar"><div id="meta">loading...</div>
<div><button id="dl">⬇ Download a_feedback.tsv</button><button id="rs">Reset</button></div></div>
<div id="app"></div>
<p class="small">Keys: <kbd>O</kbd>=OK, <kbd>N</kbd>=NO, <kbd>S</kbd>=SKIP, <kbd>←</kbd>/<kbd>→</kbd>=prev/next.</p>
</div>
<script>(function(){{
const B64="{b64}";
function b64ToJson(b64){{const bin=atob(b64);const bytes=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
return JSON.parse(new TextDecoder("utf-8").decode(bytes));}}
const data=b64ToJson(B64);let i=0;const votes={{}};
const meta=document.getElementById("meta"), app=document.getElementById("app");
function count(l){{return Object.values(votes).filter(x=>x===l).length;}}
function render(){{
 if(!data||data.length===0){{meta.textContent="No items";app.innerHTML="";return;}}
 if(i>=data.length){{meta.innerHTML="<b>Done</b> | OK="+count("OK")+" NO="+count("NO")+" SKIP="+count("SKIP");
 app.innerHTML="<h3>Finished.</h3><p>Click Download a_feedback.tsv</p>";return;}}
 const v=data[i], cur=votes[v.idx]||"—";
 meta.innerHTML="<b>"+(i+1)+"/"+data.length+"</b> dur="+v.dur.toFixed(1)+"s size="+(v.mb.toFixed(1))+"MB"
   +" | current="+cur+" | OK="+count("OK")+" NO="+count("NO")+" SKIP="+count("SKIP");
 app.innerHTML=`<video controls autoplay playsinline><source src="${{v.file}}" type="video/mp4"></video>
 <div style="margin-top:12px;">
  <button class="ok" id="ok">✅ OK (A)</button><button class="no" id="no">❌ NO</button><button id="sk">⏭ SKIP</button>
  <button id="pv">⬅ Prev</button><button id="nx">Next ➡</button></div>
 <p class="small">${{v.path}}</p>`;
 document.getElementById("ok").onclick=()=>{{votes[v.idx]="OK";i++;render();}};
 document.getElementById("no").onclick=()=>{{votes[v.idx]="NO";i++;render();}};
 document.getElementById("sk").onclick=()=>{{votes[v.idx]="SKIP";i++;render();}};
 document.getElementById("pv").onclick=()=>{{i=Math.max(i-1,0);render();}};
 document.getElementById("nx").onclick=()=>{{i=Math.min(i+1,data.length);render();}};
}}
function downloadTSV(){{
 const ts=new Date().toISOString().replace("T"," ").slice(0,19);
 const lines=["ts\tlabel\tscore\tpath"];
 for(const v of data){{const label=votes[v.idx]||"SKIP"; if(label==="SKIP") continue;
   lines.push(ts+"\\t"+label+"\\t0\\t"+v.path);}}
 const blob=new Blob([lines.join("\\n")+"\\n"],{{type:"text/tab-separated-values;charset=utf-8"}});
 const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="a_feedback.tsv";document.body.appendChild(a);a.click();a.remove();
}}
document.getElementById("dl").onclick=downloadTSV;
document.getElementById("rs").onclick=()=>{{for(const k in votes) delete votes[k]; i=0; render();}};
window.addEventListener("keydown",(e)=>{{const k=e.key.toLowerCase();
 if(k==="o"){{votes[data[i].idx]="OK";i++;render();}}
 else if(k==="n"){{votes[data[i].idx]="NO";i++;render();}}
 else if(k==="s"){{votes[data[i].idx]="SKIP";i++;render();}}
 else if(e.key==="ArrowLeft"){{i=Math.max(i-1,0);render();}}
 else if(e.key==="ArrowRight"){{i=Math.min(i+1,data.length);render();}}
}});
render();
}})();</script></body></html>"""

def main():
    cfg = load_cfg()
    mix = Path(cfg["tg_mix_dir"])

    folders = [p for p in mix.iterdir() if p.is_dir()]
    allow = []
    # whitelist first
    for f in folders:
        if f.name in ALLOW_FOLDERS:
            allow.append(f)
    # fallback: keys
    if not allow:
        for f in folders:
            n = f.name.lower()
            if any(k in n for k in MEME_KEYS):
                allow.append(f)

    if not allow:
        raise SystemExit("No meme folders matched (check ALLOW_FOLDERS / MEME_KEYS).")

    vids = []
    for f in allow:
        vids.extend(f.rglob("*.mp4"))

    now = time.time()
    cand = []
    for p in vids:
        try:
            st = p.stat()
            mb = st.st_size / (1024*1024)
            if mb < MIN_MB or mb > MAX_MB:
                continue
            mtime = st.st_mtime
        except Exception:
            continue

        d = ffprobe_dur(p)
        if d is None or d < MIN_SEC or d > MAX_SEC:
            continue

        # recency score: newer -> closer to 1
        age_days = max(0.0, (now - mtime) / 86400.0)
        rec = 1.0 / (1.0 + age_days)   # 0..1-ish
        rnd = random.random()
        score = RECENCY_WEIGHT * rec + (1.0 - RECENCY_WEIGHT) * rnd

        cand.append((score, rec, rnd, d, mb, p))

    if not cand:
        raise SystemExit("No candidates after filters. Loosen MIN/MAX or check folders.")

    random.seed(SEED)
    cand.sort(reverse=True, key=lambda x: x[0])
    cand = cand[:POOL]
    # add a bit of shuffle inside pool to avoid monotony
    random.shuffle(cand)
    cand = cand[:TOPN]

    out_dir = ROOT / "out" / "review_A_offline"
    vid_dir = out_dir / "videos"
    vid_dir.mkdir(parents=True, exist_ok=True)

    data = []
    idx = 0
    for _, _, _, d, mb, src in cand:
        idx += 1
        name = f"a_{idx:03d}.mp4"
        dst = vid_dir / name
        copy2(src, dst)
        data.append({
            "idx": idx,
            "file": str(Path("out") / "review_A_offline" / "videos" / name).replace("\\", "/"),
            "dur": float(d),
            "mb": float(mb),
            "path": str(src),
        })

    js = json.dumps(data, ensure_ascii=False)
    b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")

    out_html = ROOT / "review_A.html"
    out_html.write_text(build_html(b64), encoding="utf-8")

    print("OK. meme folders:", len(allow))
    print("OK. candidates:", len(cand), "shown:", len(data))
    print("OK. HTML:", out_html)
    print("OK. videos folder:", vid_dir)

if __name__ == "__main__":
    main()
