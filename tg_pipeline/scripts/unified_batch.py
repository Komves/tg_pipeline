from pathlib import Path
import json, shutil, re
from datetime import datetime

OUT = Path("out") / "batches" / "unified" / datetime.utcnow().strftime("%Y-%m-%d")
ASSETS = OUT / "assets"

M_BASE = Path("out/tmp/m_review_local")
M_DATA = M_BASE / "data.js"

A_VIDEO_RANK = Path("out/reports/a_video_smart.jsonl")
B_RANK = Path("out/reports/b_rank_mix_audio.jsonl")

RAW_ROOT = Path("data/tg/raw/MIX")

def mkdir(p): p.mkdir(parents=True, exist_ok=True)
def rel_asset(dst: Path) -> str: return "assets/" + dst.name

def read_jsonl(p: Path):
    items = []
    if not p.exists(): return items
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except:
            pass
    return items

def parse_m_data_js_paths():
    if not M_DATA.exists():
        return []
    txt = M_DATA.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"(\[.*\])", txt, flags=re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
    except:
        return []
    paths = []
    if isinstance(arr, list):
        for it in arr:
            if isinstance(it, dict):
                p = it.get("path") or it.get("src") or it.get("file") or it.get("img") or it.get("image")
                if isinstance(p, str) and p:
                    paths.append(p)
            elif isinstance(it, str):
                paths.append(it)
    return paths

def resolve_meme_path(s: str) -> Path:
    p = Path(str(s).replace("\\\\", "\\").strip())
    if p.exists():
        return p
    return (M_BASE / p).resolve()

# Ключевой резолвер для видео:
# - пытаемся открыть как есть (относительно корня)
# - если не нашли: ищем по basename внутри RAW_ROOT (архивный layout)
_video_cache = {}
def resolve_video_path(s: str) -> Path:
    p = Path(str(s).replace("\\\\", "\\").strip())
    if p.exists():
        return p
    p2 = (Path(".") / p).resolve()
    if p2.exists():
        return p2

    name = p.name
    if name in _video_cache:
        return _video_cache[name]

    if RAW_ROOT.exists():
        hits = list(RAW_ROOT.rglob(name))
        if hits:
            _video_cache[name] = hits[0]
            return hits[0]

    return p2  # fallback (не существует)

def copy_items_from_paths(src_list, category, limit, resolver):
    items = []
    for s in src_list:
        if len(items) >= limit:
            break
        srcp = resolver(s)
        if not srcp.exists():
            continue
        dst = ASSETS / f"{category}_{len(items)}{srcp.suffix}"
        shutil.copy2(srcp, dst)
        items.append({
            "id": f"{category}_{len(items)}",
            "category": category,
            "path": rel_asset(dst),
            "src": str(srcp)
        })
    return items

def pick_from_rank(rank_path: Path, limit: int):
    rows = read_jsonl(rank_path)
    picked = []
    for r in rows:
        p = r.get("path")
        if not isinstance(p, str) or not p:
            continue
        try:
            score = float(r.get("score", 0.0))
        except:
            score = 0.0
        picked.append((score, p))
    picked.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in picked[:limit]]

def main():
    mkdir(ASSETS)

    memes = copy_items_from_paths(parse_m_data_js_paths(), "A_MEME", 10, resolve_meme_path)
    av    = copy_items_from_paths(pick_from_rank(A_VIDEO_RANK, 5), "A_VIDEO", 5, resolve_video_path)
    b     = copy_items_from_paths(pick_from_rank(B_RANK, 2), "B_EROTIC", 2, resolve_video_path)

    all_items = memes + av + b

    with open(OUT/"manifest.jsonl", "w", encoding="utf-8") as f:
        for x in all_items:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Unified 10/5/2</title>
<style>
  body{{font-family:Arial;margin:16px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
  .card{{border:1px solid #ddd;border-radius:10px;padding:10px}}
  img,video{{width:100%;border-radius:8px}}
  .small{{font-size:12px;opacity:.7;word-break:break-word}}
</style>
</head><body>
<h1>Unified 10/5/2</h1>
<p>Picked: memes={len(memes)} video={len(av)} b={len(b)}</p>
<div class="grid">
"""
    for it in all_items:
        p = it["path"].lower()
        if p.endswith((".jpg", ".jpeg", ".png", ".webp")):
            media = f'<img src="{it["path"]}">'
        else:
            media = f'<video controls preload="metadata"><source src="{it["path"]}" type="video/mp4"></video>'
        html += f'<div class="card">{media}<div class="small">{it["category"]}<br>{it["src"]}</div></div>\n'

    html += "</div></body></html>"
    (OUT/"index.html").write_text(html, encoding="utf-8")

    print("[OK] memes_picked=", len(memes), "av_picked=", len(av), "b_picked=", len(b))

if __name__ == "__main__":
    main()
