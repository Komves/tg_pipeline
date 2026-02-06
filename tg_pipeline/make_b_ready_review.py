from pathlib import Path

READY = Path(r".\data\tg\raw\B_ready")
OUT = Path(r".\out\reports\b_ready_review.tsv")

files = sorted([p for p in READY.glob("*.mp4")])

OUT.parent.mkdir(parents=True, exist_ok=True)

with open(OUT, "w", encoding="utf-8") as f:
    f.write("rank\tstatus\tpath\tnote\n")
    for i, p in enumerate(files, 1):
        f.write(f"{i}\t\t{p}\t\n")

print("written:", OUT.resolve())
print("count:", len(files))
