import json, os, hashlib, shutil
from pathlib import Path

SRC_JSONL = Path(r".\out\reports\b_rank_mix_fast_live.jsonl")
DST_DIR   = Path(r".\data\tg\raw\B_candidates")
THRESH = 0.84

DST_DIR.mkdir(parents=True, exist_ok=True)

def safe_name(p: str) -> str:
    # уникальное имя: score + hash(path) + basename
    h = hashlib.md5(p.encode("utf-8", errors="ignore")).hexdigest()[:8]
    base = Path(p).name
    return f"{h}__{base}"

def hardlink_or_copy(src: Path, dst: Path) -> str:
    try:
        if dst.exists():
            return "exists"
        os.link(src, dst)   # hardlink
        return "hardlink"
    except Exception:
        shutil.copy2(src, dst)
        return "copy"

rows = []
with open(SRC_JSONL, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        try:
            r = json.loads(line)
            if float(r.get("score", 0.0)) >= THRESH:
                rows.append((float(r["score"]), r["path"]))
        except:
            pass

rows.sort(reverse=True, key=lambda x: x[0])

linked = copied = exists = missing = 0
written = 0

index_path = DST_DIR / "_index.tsv"
with open(index_path, "w", encoding="utf-8") as idx:
    idx.write("rank\tscore\tsrc\tdst\tmode\n")
    for i, (score, sp) in enumerate(rows, 1):
        src = Path(sp)
        if not src.exists():
            missing += 1
            continue
        dst = DST_DIR / f"{i:03d}_{score:.4f}__{safe_name(sp)}"
        mode = hardlink_or_copy(src, dst)
        if mode == "hardlink": linked += 1
        elif mode == "copy": copied += 1
        elif mode == "exists": exists += 1
        idx.write(f"{i}\t{score:.4f}\t{src}\t{dst}\t{mode}\n")
        written += 1

print("[done]")
print("thresh:", THRESH)
print("candidates:", len(rows))
print("written:", written)
print("hardlink:", linked, "copy:", copied, "exists:", exists, "missing:", missing)
print("dst_dir:", DST_DIR.resolve())
print("index:", index_path.resolve())
