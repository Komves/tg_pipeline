import html
from pathlib import Path

CANON = Path(r".\out\index\b_candidates_canonical.txt")
OUT = Path(r".\out\reports\b_queue_review.html")

paths = [p.strip() for p in CANON.read_text(encoding="utf-8", errors="ignore").splitlines() if p.strip()]

cards = []
for i, p in enumerate(paths, 1):
    url = "file:///" + p.replace("\\", "/")
    cards.append(f"""
    <div class="card">
      <div class="hdr">#{i:04d}</div>
      <a class="btn" href="{html.escape(url)}">▶ Открыть видео</a>
      <div class="path">{html.escape(p)}</div>
      <div class="hint">Команды: <code>.\b_mark.ps1 {i} ok</code> | <code>.\b_mark.ps1 {i} no</code> | <code>.\b_mark.ps1 {i} later</code></div>
    </div>
    """)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>B Review Queue</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; margin:16px; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(520px, 1fr)); gap: 12px; }}
.card {{ background:#1c1c1c; padding:12px; border-radius:12px; }}
.hdr {{ font-size:14px; color:#fff; margin-bottom:8px; }}
.path {{ font-size:12px; color:#aaa; margin-top:8px; word-break: break-all; }}
.hint {{ font-size:12px; color:#777; margin-top:10px; }}
.btn {{ display:inline-block; color:#4ea3ff; text-decoration:none; margin-top:6px; }}
code {{ background:#000; padding:2px 6px; border-radius:6px; }}
</style>
<h2>B Review Queue (canonical: {len(paths)})</h2>
<p>Открывай видео и отмечай решением через PowerShell: <code>.\b_mark.ps1 N ok/no/later</code></p>
<div class="grid">{''.join(cards)}</div>
</html>""", encoding="utf-8")

print("written:", OUT.resolve())
print("count:", len(paths))
