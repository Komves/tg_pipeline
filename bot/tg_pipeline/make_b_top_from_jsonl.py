import json, html
from pathlib import Path

p = Path(r".\out\reports\b_rank_mix_fast_live.jsonl")
out = Path(r".\out\reports\b_top_mix_fast_live_thr084.html")

THRESH = 0.84
TOP_N = 300

rows = []
with open(p, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        try:
            r = json.loads(line)
            if "score" in r and "path" in r:
                rows.append({"path": r["path"], "score": float(r["score"])})
        except:
            pass

rows.sort(key=lambda r: r["score"], reverse=True)
top = [r for r in rows if r["score"] >= THRESH][:TOP_N]

cards = []
for r in top:
    url = "file:///" + r["path"].replace("\\", "/")
    cards.append(
        "<div class='card'>"
        f"<div class='score'>score: {r['score']:.4f}</div>"
        f"<a href='{html.escape(url)}'>▶ Открыть видео</a>"
        f"<div class='path'>{html.escape(r['path'])}</div>"
        "</div>"
    )

doc = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><title>B rank MIX FAST</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 10px; }}
.card {{ background:#1c1c1c; padding:10px; border-radius:10px; }}
a {{ color:#4ea3ff; text-decoration:none; font-size:13px; }}
.score {{ font-size:12px; color:#aaa; margin-bottom:6px; }}
.path {{ font-size:11px; color:#888; margin-top:6px; word-break: break-all; }}
</style>
</head>
<body>
<h2>B rank MIX FAST (thr={THRESH})</h2>
<div>total_scored: {len(rows)} | kept: {len(top)}</div>
<div class="grid">{''.join(cards)}</div>
</body></html>
"""

out.write_text(doc, encoding="utf-8")
print("kept:", len(top))
print("written:", out.resolve())
print("top10:")
for r in rows[:10]:
    print(f"{r['score']:.4f}  {r['path']}")
