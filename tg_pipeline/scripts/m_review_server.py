import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("out/tmp/m_review_local").resolve()
FEEDBACK = Path("out/logs/a_feedback_master.tsv")
PORT = 8765

INDEX_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Meme review</title>
<style>
  body{font-family:Arial;margin:16px}
  .wrap{max-width:980px;margin:0 auto}
  img{width:100%;border-radius:10px;border:1px solid #ddd}
  .btns button{margin:6px 6px 6px 0;padding:10px 14px;border-radius:10px;border:1px solid #ccc;cursor:pointer}
  .small{font-size:12px;opacity:.7;word-break:break-word;margin-top:6px}
</style>
</head><body><div class="wrap">
<h2>Meme review</h2>
<script src="data.js"></script>
<div id="app"></div>
<script>
const items = (window.BATCH_ITEMS || []).map(x => ({src:x.src, orig:x.orig||""}));
let idx=0;
function cur(){ return items[idx]; }
function render(){
  const it = cur();
  if(!it){ document.getElementById('app').innerHTML='<b>DONE</b>'; return; }
  document.getElementById('app').innerHTML = `
    <img src="${it.src}">
    <div class="btns">
      <button onclick="send('SUPER')">SUPER</button>
      <button onclick="send('OK')">OK</button>
      <button onclick="send('NO')">NO</button>
      <button onclick="send('BAN')">BAN</button>
      <button onclick="next()">SKIP</button>
    </div>
    <div class="small">orig=${it.orig}</div>`;
}
async function send(label){
  const it=cur();
  const payload={ts:new Date().toISOString(),label:label,score:"",path:(it.orig||it.src)};
  const r = await fetch('/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!r.ok){ alert('feedback failed: '+r.status); return; }
  next();
}
function next(){ idx++; render(); }
render();
</script></div></body></html>"""

def ensure_index():
    ROOT.mkdir(parents=True, exist_ok=True)
    p = ROOT / "index.html"
    if not p.exists():
        p.write_text(INDEX_HTML, encoding="utf-8")

class H(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = urlparse(path).path.lstrip("/")
        return str((ROOT / p).resolve())

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _safe_write(self, b: bytes):
        try:
            self.wfile.write(b)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        if self.path != "/feedback":
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length","0"))
            raw = self.rfile.read(n)
            obj = json.loads(raw.decode("utf-8", errors="ignore"))
            ts = str(obj.get("ts","")).replace("\t"," ").replace("\n"," ").replace("\r"," ")
            label = str(obj.get("label","")).replace("\t"," ").replace("\n"," ").replace("\r"," ")
            score = str(obj.get("score","")).replace("\t"," ").replace("\n"," ").replace("\r"," ")
            path = str(obj.get("path","")).replace("\t"," ").replace("\n"," ").replace("\r"," ")
        except Exception:
            self.send_response(400); self.end_headers(); return

        FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
        if not FEEDBACK.exists():
            FEEDBACK.write_text("ts\tlabel\tscore\tpath\n", encoding="utf-8")
        with FEEDBACK.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{label}\t{score}\t{path}\n")

        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.end_headers()
        self._safe_write(b'{"ok":true}')

class S(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

def main():
    ensure_index()
    srv = S(("127.0.0.1", PORT), H)
    print(f"[M_SERVER] serving {ROOT} on http://127.0.0.1:{PORT}/index.html")
    srv.serve_forever()

if __name__ == "__main__":
    main()
