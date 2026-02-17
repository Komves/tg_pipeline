from __future__ import annotations
import json, os, time, re, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
RANK = ROOT / "out" / "reports" / "a_rank_memes.jsonl"
FEEDBACK = ROOT / "out" / "feedback" / "a_feedback.jsonl"

def load_items(n=400):
    items=[]
    with open(RANK, "r", encoding="utf-8") as f:
        for line in f:
            if len(items)>=n: break
            line=line.strip()
            if not line: continue
            try:
                o=json.loads(line)
            except:
                continue
            p=o.get("path") or o.get("video_path") or o.get("file") or o.get("src")
            s=o.get("score")
            if p:
                items.append({"path":p, "score":s})
    return items

ITEMS = []

def html(items):
    payload=json.dumps(items,ensure_ascii=False)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>A Review</title>
<style>
body{{font-family:Arial;margin:16px}}
button{{font-size:16px;padding:10px 14px;margin-right:8px;cursor:pointer}}
video{{max-width:min(980px,100%);display:block;margin-top:12px;border-radius:10px;background:#000}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px}}
.small{{color:#666;font-size:13px}}
</style></head>
<body>
<h3>TG A Review UI</h3>
<div>
  <button onclick="vote('ok')">👍 OK (K)</button>
  <button onclick="vote('no')">👎 NO (J)</button>
  <button onclick="next()">⏭ Skip (S)</button>
  <span class="small">keys: K=OK, J=NO, S=skip</span>
</div>

<video id="v" controls autoplay loop playsinline></video>

<div style="margin-top:10px">
  <div><b>Index:</b> <span id="idx"></span>/<span id="tot"></span></div>
  <div><b>Score:</b> <span id="sc"></span></div>
  <div><b>Path:</b> <code id="p"></code></div>
  <div class="small">feedback → <code>out\\feedback\\a_feedback.jsonl</code></div>
</div>

<script>
const items = {payload};
let i=0;

const v=document.getElementById('v');
const idx=document.getElementById('idx');
const tot=document.getElementById('tot');
const sc=document.getElementById('sc');
const p=document.getElementById('p');

tot.textContent = items.length;

function show() {{
  const it = items[i];
  idx.textContent = (i+1);
  sc.textContent = (it.score===null||it.score===undefined) ? "-" : String(it.score);
  p.textContent = it.path;

  // IMPORTANT: serve through /video endpoint (supports abs paths + URL encoding + Range)
  v.src = "/video?p=" + encodeURIComponent(it.path);
  v.play().catch(()=>{{}});
}}

async function vote(label) {{
  const it = items[i];
  await fetch("/fb", {{
    method:"POST",
    headers:{{"Content-Type":"application/json"}},
    body: JSON.stringify({{label:label, path:it.path, score:it.score, ts:new Date().toISOString()}})
  }});
  next();
}}

function next() {{
  i = Math.min(i+1, items.length-1);
  show();
}}

document.addEventListener('keydown', (e)=>{{
  const k=e.key.toLowerCase();
  if(k==='k') vote('ok');
  else if(k==='j') vote('no');
  else if(k==='s') next();
}});

show();
</script>
</body></html>"""

def safe_resolve_path(p: str) -> Path:
    # Accept relative paths under project root OR absolute Windows paths like C:\...
    p = p.strip().strip('"').strip("'")
    # normalize slashes
    p = p.replace("/", "\\")
    # absolute windows path?
    if re.match(r"^[A-Za-z]:\\", p):
        return Path(p)
    # relative to project root
    return (ROOT / p).resolve()

def send_range_file(handler: BaseHTTPRequestHandler, file_path: Path):
    if not file_path.exists() or not file_path.is_file():
        body = f"NOT FOUND: {file_path}".encode("utf-8", errors="ignore")
        handler.send_response(404)
        handler.send_header("Content-Type","text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return

    ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    size = file_path.stat().st_size
    range_header = handler.headers.get("Range")

    # default: full content
    start = 0
    end = size - 1
    status = 200

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            status = 206

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Accept-Ranges", "bytes")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Content-Length", str(length))
    handler.end_headers()

    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = length
        chunk = 1024 * 256
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data:
                break
            handler.wfile.write(data)
            remaining -= len(data)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = html(ITEMS).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/video":
            qs = parse_qs(parsed.query)
            p = qs.get("p", [""])[0]
            p = unquote(p)
            fp = safe_resolve_path(p)
            send_range_file(self, fp)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/fb":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length","0"))
        raw = self.rfile.read(n) if n>0 else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8"))
        except:
            self.send_response(400); self.end_headers(); return
        FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.send_response(200); self.end_headers()

def main():
    os.chdir(ROOT)
    if not RANK.exists():
        raise SystemExit(f"Нет файла ранка: {RANK}")
    global ITEMS
    ITEMS = load_items(500)
    print("UI -> http://127.0.0.1:8787/")
    print("Rank:", RANK)
    print("Feedback:", FEEDBACK)
    ThreadingHTTPServer(("127.0.0.1", 8787), H).serve_forever()

if __name__ == "__main__":
    main()
