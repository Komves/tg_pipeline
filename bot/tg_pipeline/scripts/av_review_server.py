import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("out/tmp/av_review_local").resolve()
FEEDBACK = Path("out/logs/a_video_feedback_master.tsv")

class H(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = urlparse(path).path.lstrip("/")
        return str((ROOT / p).resolve())

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):
        # можно оставить, чтобы видеть POST/GET
        super().log_message(format, *args)

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
            ts = str(obj.get("ts",""))
            label = str(obj.get("label",""))
            score = str(obj.get("score",""))
            path = str(obj.get("path",""))
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
    # глушим именно 10054/pipe на уровне сервера
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

def main():
    if not ROOT.exists():
        raise SystemExit(f"missing {ROOT} (run av_next_review.py first)")
    srv = S(("127.0.0.1", 8011), H)
    print("[AV_SERVER] serving", ROOT, "on http://127.0.0.1:8011")
    srv.serve_forever()

if __name__ == "__main__":
    main()
