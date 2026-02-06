import os
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime

ROOT = Path(__file__).resolve().parent

def load_cfg():
    cfg_path = ROOT / "out" / "config" / "engine.local.json"
    return json.loads(cfg_path.read_text(encoding="utf-8-sig"))

def read_top(rank_jsonl, topn):
    rows = []
    for line in rank_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("status") != "ok":
            continue
        rows.append({"score": float(o["score"]), "path": o["path"]})
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:topn]

class App:
    def __init__(self, topn):
        cfg = load_cfg()
        self.rank_jsonl = Path(cfg["reports_dir"]) / "b_rank_daily.jsonl"
        self.logs_dir = Path(cfg["logs_dir"])
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        lbl = (os.environ.get("REVIEW_LABEL") or "B").strip().upper()
fn = "a_feedback.tsv" if lbl == "A" else "b_feedback.tsv"
self.fb_path = self.logs_dir / fn
        if not self.fb_path.exists():
            self.fb_path.write_text("ts\tlabel\tscore\tpath\n", encoding="utf-8")

        self.items = read_top(self.rank_jsonl, topn)
        self.idx = 0

    def current(self):
        if 0 <= self.idx < len(self.items):
            return self.items[self.idx]
        return None

    def vote(self, label):
        item = self.current()
        if not item:
            return
        if label in ("OK", "NO"):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{ts}\t{label}\t{item['score']:.4f}\t{item['path']}\n"
            with open(self.fb_path, "a", encoding="utf-8") as f:
                f.write(line)
        self.idx += 1

HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>TG Review</title>
<style>
body { font-family: Arial; margin: 20px; }
video { width: 100%; max-height: 70vh; background: black; }
button { font-size: 16px; padding: 10px 16px; margin-right: 10px; }
.path { font-family: monospace; font-size: 12px; color: #555; }
</style>
</head>
<body>
<h3>Item INDEX / TOTAL | score=SCORE</h3>
<video controls autoplay>
  <source src="/file?path=FILE" type="video/mp4">
</video>
<p class="path">PATH</p>
<form method="POST" action="/vote">
  <button name="label" value="OK">OK</button>
  <button name="label" value="NO">NO</button>
  <button name="label" value="SKIP">SKIP</button>
</form>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    app = None

    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/":
            item = self.app.current()
            if not item:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h2>Done</h2>")
                return

            html = HTML
            html = html.replace("INDEX", str(self.app.idx + 1))
            html = html.replace("TOTAL", str(len(self.app.items)))
            html = html.replace("SCORE", f"{item['score']:.4f}")
            html = html.replace("FILE", item["path"].replace("\\\\", "/"))
            html = html.replace("PATH", item["path"])

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return

        if u.path == "/file":
            qs = parse_qs(u.query)
            p = qs.get("path", [""])[0].replace("/", "\\\\")
            fp = Path(p)
            if not fp.exists():
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(fp.stat().st_size))
            self.end_headers()
            with open(fp, "rb") as f:
                self.wfile.write(f.read())
            return

        self.send_error(404)

    def do_POST(self):
        if self.path == "/vote":
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length).decode("utf-8")
            form = parse_qs(data)
            label = form.get("label", ["SKIP"])[0]
            self.app.vote(label)

            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=10)
    ap.add_argument("--port", type=int, default=8792)
    args = ap.parse_args()

    app = App(args.topn)
    Handler.app = app

    host = "127.0.0.1"
    httpd = HTTPServer((host, args.port), Handler)

    print(f"[review-ui] items={len(app.items)} rank={app.rank_jsonl}")
    print(f"[review-ui] feedback={app.fb_path}")
    print(f"[review-ui] LISTENING on http://{host}:{args.port}/")

    httpd.serve_forever()

if __name__ == "__main__":
    main()

