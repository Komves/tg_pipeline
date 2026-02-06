from pathlib import Path

FEEDBACK = Path("out/logs/a_feedback_master.tsv")
OUT = Path("out/logs/a_meme_banned_items.txt")

def main():
    if not FEEDBACK.exists():
        print("[M_BAN_ITEM] no feedback"); return

    bans = set()
    for line in FEEDBACK.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        _ts, label, _score, path = parts
        if label.strip().upper() == "BAN":
            bans.add(path.strip())

    old = set()
    if OUT.exists():
        old = set(x.strip() for x in OUT.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip())

    merged = sorted(x for x in (old | bans) if x.strip())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    print(f"[M_BAN_ITEM] total={len(merged)} added={len((old|bans)-old)} -> {OUT}")

if __name__ == "__main__":
    main()
