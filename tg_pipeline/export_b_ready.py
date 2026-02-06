from pathlib import Path
import os, shutil, hashlib

SRC_LIST = Path(r".\out\index\b_candidates_canonical.txt")
DST_DIR  = Path(r".\data\tg\raw\B_ready")
N = 20

DST_DIR.mkdir(parents=True, exist_ok=True)

paths = [p.strip() for p in SRC_LIST.read_text(encoding="utf-8", errors="ignore").splitlines() if p.strip()]

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

picked = paths[:N]
hardlink = copy = exists = missing = 0

for i, p in enumerate(picked, 1):
    src = Path(p)
    if not src.exists():
        missing += 1
        continue
    dst = DST_DIR / f"{i:02d}_{safe_name(p)}"
    mode = hardlink_or_copy(src, dst)
    if mode == "hardlink": hardlink += 1
    elif mode == "copy": copy += 1
    else: exists += 1

print("[done]")
print("picked:", len(picked))
print("hardlink:", hardlink, "copy:", copy, "exists:", exists, "missing:", missing)
print("dst_dir:", DST_DIR.resolve())
