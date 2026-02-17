import json, os, shutil
from pathlib import Path
from datetime import datetime

RANK = Path("out/reports/a_rank_video_24h_audio.jsonl")
FEEDBACK = Path("out/logs/a_video_feedback_master.tsv")

OUT_DIR = Path("out/tmp/av_review_local")
ASSETS = OUT_DIR / "assets"

LIMIT = 70

def read_jsonl(p: Path):
    out = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except:
            pass
    return out

def load_seen(feedback_path: Path):
    seen = set()
    if not feedback_path.exists():
        return seen
    for line in feedback_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("ts"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            seen.add(parts[3].strip())
    return seen

def resolve_video(path_str: str) -> Path:
    p = Path(path_str.replace("\\\\","\\"))
    if p.exists():
        return p
    p2 = (Path(".") / p).resolve()
    if p2.exists():
        return p2
    # fallback: search by basename inside RAW
    raw = Path("data/tg/raw/MIX")
    hits = list(raw.rglob(p.name))
    if hits:
        return hits[0]
    return p2

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    rows = read_jsonl(RANK)
    seen = load_seen(FEEDBACK)

    picked = []
    for r in rows:
        if len(picked) >= LIMIT:
            break
        p = r.get("path")
        if not isinstance(p, str) or not p:
            continue
        if p in seen:
            continue
        src = resolve_video(p)
        if not src.exists():
            continue
        picked.append({
            "src": str(src).replace("/", "\\"),
            "rank_path": p,
            "score": float(r.get("score", 0.0))
        })

    # clean old assets
    for f in ASSETS.glob("*"):
        try: f.unlink()
        except: pass

    items = []
    for i, it in enumerate(picked):
        src = Path(it["src"])
        dst = ASSETS / f"v_{i:03d}{src.suffix}"
        shutil.copy2(src, dst)
        items.append({
            "i": i,
            "file": f"assets/{dst.name}",
            "src": it["src"],
            "rank_path": it["rank_path"],
            "score": it["score"]
        })

    data_js = "window.ITEMS=" + json.dumps(items, ensure_ascii=False) + ";"
    (OUT_DIR / "data.js").write_text(data_js, encoding="utf-8")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>A-VIDEO review</title>
<style>
body{{font-family:Arial;margin:16px}}
.wrap{{max-width:980px;margin:0 auto}}
video{{width:100%;border-radius:10px;background:#000}}
.btns button{{margin:6px 6px 6px 0;padding:10px 14px;border-radius:10px;border:1px solid #ccc;cursor:pointer}}
.small{{font-size:12px;opacity:.7;word-break:break-word;margin-top:6px}}
</style>
</head><body><div class="wrap">
<h2>A-VIDEO review (items: {len(items)})</h2>
<script src="data.js"></script>
<div id="app"></div>
<script>
let idx=0;
function render(){{
  const it = window.ITEMS[idx];
  if(!it){{ document.getElementById('app').innerHTML='<b>DONE</b>'; return; }}
  document.getElementById('app').innerHTML = `
    <video controls preload="metadata"><source src="${{it.file}}" type="video/mp4"></video>
    <div class="btns">
      <button onclick="send('SUPER')">SUPER</button>
      <button onclick="send('OK')">OK</button>
      <button onclick="send('NO')">NO</button>
      <button onclick="send('BAN')">BAN</button>
      <button onclick="next()">SKIP</button>
    </div>
    <div class="small">score=${{it.score}}<br>rank_path=${{it.rank_path}}<br>src=${{it.src}}</div>
  `;
}}
async function send(label){{
  const it = window.ITEMS[idx];
  const payload = {{ ts: new Date().toISOString(), label, score: it.score, path: it.rank_path }};
  try {{
    await fetch('/feedback', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(payload)
    }});
  }} catch(e) {{
    alert('feedback POST failed: ' + e);
    return;
  }}
  next();
}}
function next(){{ idx++; render(); }}
render();
</script>
</div></body></html>
"""
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")

    print(f"[AV_REVIEW] wrote={len(items)} -> {OUT_DIR}")

if __name__ == "__main__":
    main()
