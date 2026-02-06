from pathlib import Path
import os, shutil, hashlib

TSV = Path(r".\out\reports\b_ready_review.tsv")
DST = Path(r".\data\tg\raw\B_post")

DST.mkdir(parents=True, exist_ok=True)

rows = []
with open(TSV, "r", encoding="utf-8", errors="ignore") as f:
    header = f.readline()
    for line in f:
        if not line.strip():
            continue
        rank, status, path, note = (line.split("\t") + ["","","",""])[:4]
        status = status.strip().lower()
        path = path.strip()
        if status == "ok" and path:
            rows.append(path)

def safe_name(p: str) -> str:
    h = hashlib.md5(p.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{h}__{Path(p).name}"

def hardlink_or_copy(src: Path, dst: Path) -> str:
    try:
        if dst.exists():
            return "exists"
        os.link(src, dst)
        return "hardlink"
    except Exception:
        shutil.copy2(src, dst)
        return "copy"

hardlink = copy = exists = missing = 0

for i, p in enumerate(rows, 1):
    src = Path(p)
    if not src.exists():
        missing += 1
        continue
    dst = DST / f"{i:02d}_{safe_name(p)}"
    mode = hardlink_or_copy(src, dst)
    if mode == "hardlink": hardlink += 1
    elif mode == "copy": copy += 1
    else: exists += 1

print("[done]")
print("ok_count:", len(rows))
print("hardlink:", hardlink, "copy:", copy, "exists:", exists, "missing:", missing)
print("dst_dir:", DST.resolve())
