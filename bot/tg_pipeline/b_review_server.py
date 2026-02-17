import os, time, json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
CANON = ROOT / r"out\index\b_candidates_canonical.txt"
LOG   = ROOT / r"out\logs\b_feedback.tsv"

HOST = "127.0.0.1"
PORT = 8765

def load_paths():
    if not CANON.exists():
        return []
    return [p.strip() for p in CANON.read_text(encoding="utf-8", errors="ignore").splitlines() if p.strip()]

def ts():
    # ISO-ish without microseconds
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def html_page(i, total, path):
    esc = lambda s: (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;"))
    i1 = i + 1
    prev_i = max(1, i1-1)
    next_i = min(total, i1+1)

    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>B Review #{i1}/{total}</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; margin:16px; }}
.wrap {{ max-width: 980px; margin: 0 auto; }}
h2 {{ margin: 0 0 8px 0; }}
.meta {{ color:#aaa; font-size: 13px; margin-bottom: 10px; }}
video {{ width: 100%; border-radius: 12px; background:#000; }}
.row {{ display:flex; gap:10px; margin-top:12px; flex-wrap: wrap; }}
button {{ padding:10px 14px; border-radius: 10px; border:0; cursor:pointer; font-size:14px; }}
.ok {{ background:#2e7d32; color:white; }}
.no {{ background:#c62828; color:white; }}
.later {{ background:#546e7a; color:white; }}
.nav {{ background:#333; color:#eee; }}
.small {{ color:#777; font-size: 12px; margin-top: 10px; }}
.path {{ color:#888; font-size: 12px; word-break: break-all; margin-top: 8px; }}
</style>
<div class="wrap">
  <h2>B review: #{i1}/{total}</h2>
  <div class="meta">Hotkeys: <b>O</b>=OK, <b>N</b>=NO, <b>L</b>=LATER, <b>←/→</b>=prev/next</div>

  <video controls autoplay preload="metadata">
    <source src="/video?id={i1}" type="video/mp4">
  </video>

  <div class="row">
    <button class="ok" onclick="mark('ok')">OK (O)</button>
    <button class="no" onclick="mark('no')">NO (N)</button>
    <button class="later" onclick="mark('later')">LATER (L)</button>

    <button class="nav" onclick="go({prev_i})">← Prev</button>
    <button class="nav" onclick="go({next_i})">Next →</button>
    <button class="nav" onclick="location.href='/stats'">Stats</button>
  </div>

  <div class="path">{esc(path)}</div>
  <div class="small">Лог пишется в: {esc(str(LOG))}</div>
</div>

<script>
const cur = {i1};
function go(id) {{
  location.href = "/?id=" + id;
}}

async function mark(label) {{
  const r = await fetch("/mark?id=" + cur + "&label=" + label, {{method:"POST"}});
  const t = await r.text();
  // после отметки — сразу на следующий
  go(Math.min({total}, cur+1));
}}

document.addEventListener("keydown", (e) => {{
  const k = e.key.toLowerCase();
  if (k === "o") mark("ok");
  else if (k === "n") mark("no");
  else if (k === "l") mark("later");
  else if (e.key === "ArrowLeft") go(Math.max(1, cur-1));
  else if (e.key === "ArrowRight") go(Math.min({total}, cur+1));
}});
</script>
</html>"""

def parse_range(range_header, size):
    # supports: bytes=start-end
    if not range_header or not range_header.startswith("bytes="):
        return None
    try:
        part = range_header.split("=",1)[1].strip()
        start_s, end_s = part.split("-",1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
        if start < 0: start = 0
        if end >= size: end = size - 1
        if end < start: return None
        return start, end
    except:
        return None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        paths = load_paths()
        total = len(paths)
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/":
            if total == 0:
                self.send_response(200)
                self.send_header("Content-Type","text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"No canonical paths. Check out\\index\\b_candidates_canonical.txt")
                return

            i1 = int(q.get("id",[1])[0])
            if i1 < 1: i1 = 1
            if i1 > total: i1 = total
            path = paths[i1-1]

            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_page(i1-1, total, path).encode("utf-8"))
            return

        if u.path == "/video":
            if total == 0:
                self.send_error(404)
                return
            i1 = int(q.get("id",[1])[0])
            if i1 < 1 or i1 > total:
                self.send_error(404)
                return
            fp = Path(paths[i1-1])
            if not fp.exists():
                self.send_error(404)
                return

            size = fp.stat().st_size
            r = parse_range(self.headers.get("Range"), size)

            if r is None:
                self.send_response(200)
                self.send_header("Content-Type","video/mp4")
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges","bytes")
                self.end_headers()
                with open(fp, "rb") as f:
                    self.wfile.write(f.read())
                return

            start, end = r
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type","video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges","bytes")
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(length))
            return

        if u.path == "/stats":
            LOG.parent.mkdir(parents=True, exist_ok=True)
            ok=no=later=0
            if LOG.exists():
                for line in LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        lab = parts[2]
                        if lab=="ok": ok += 1
                        elif lab=="no": no += 1
                        elif lab=="later": later += 1

            body = f"""<!doctype html><meta charset="utf-8">
<style>body{{font-family:Arial;background:#111;color:#eee;margin:16px}} a{{color:#4ea3ff}}</style>
<h2>Stats</h2>
<div>ok: {ok}</div><div>no: {no}</div><div>later: {later}</div>
<p><a href="/">Back</a></p>
<p>Log: {LOG}</p>
"""
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return

        self.send_error(404)

    def do_POST(self):
        paths = load_paths()
        total = len(paths)
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/mark":
            if total == 0:
                self.send_error(400)
                return
            i1 = int(q.get("id",[0])[0])
            label = q.get("label",[""])[0]
            if i1 < 1 or i1 > total or label not in ("ok","no","later"):
                self.send_error(400)
                return
            path = paths[i1-1]

            LOG.parent.mkdir(parents=True, exist_ok=True)
            LOG.touch(exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"{ts()}\t{i1}\t{label}\t{path}\n")

            self.send_response(200)
            self.send_header("Content-Type","text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        self.send_error(404)

def main():
    print(f"[b_review] canon: {CANON}")
    print(f"[b_review] log  : {LOG}")
    print(f"[b_review] open : http://{HOST}:{PORT}/")
    httpd = HTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
