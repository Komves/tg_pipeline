from pathlib import Path
import html

lst = Path(r".\out\index\b_candidates_canonical.txt")
out = Path(r".\out\reports\b_queue_candidates.html")

paths = [p.strip() for p in lst.read_text(encoding="utf-8", errors="ignore").splitlines() if p.strip()]

cards = []
for i, p in enumerate(paths, 1):
    url = "file:///" + p.replace("\\", "/")
    cards.append(
        "<div class='card'>"
        f"<div class='n'>#{i}</div>"
        f"<a class='lnk' href='{html.escape(url)}'>▶ Открыть видео</a>"
        f"<div class='path'>{html.escape(p)}</div>"
        "</div>"
    )

doc = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>B queue</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.wrap {{ max-width: 1100px; margin: 20px auto; }}
.card {{ background:#1c1c1c; padding:12px; border-radius:10px; margin:10px 0; }}
.n {{ color:#aaa; font-size:12px; margin-bottom:6px; }}
.lnk {{ color:#4ea3ff; text-decoration:none; font-size:14px; }}
.path {{ color:#777; font-size:11px; margin-top:6px; word-break: break-all; }}
</style></head>
<body><div class="wrap">
<h2>B queue (canonical): {len(paths)}</h2>
{''.join(cards)}
</div></body></html>
"""

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(doc, encoding="utf-8")

print("written:", out.resolve())
print("count:", len(paths))
