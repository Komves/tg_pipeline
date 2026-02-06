from __future__ import annotations
from pathlib import Path
import subprocess
import time

READY_DIR = Path(r".\data\tg\raw\B_ready")
TSV_PATH  = Path(r".\out\reports\b_ready_review.tsv")

VALID = {"ok", "no", "later", "skip", "back", "quit", "q"}

def norm(p: Path) -> str:
    return str(p.resolve())

def load_tsv(tsv: Path) -> dict[str, dict]:
    data = {}
    if not tsv.exists():
        return data
    lines = tsv.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return data
    # header: rank\tstatus\tpath\tnote
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 4:
            parts.append("")
        rank, status, path, note = parts[0], parts[1], parts[2], parts[3]
        if path:
            data[path] = {"rank": rank, "status": status, "note": note}
    return data

def write_tsv(tsv: Path, rows: list[dict]):
    tsv.parent.mkdir(parents=True, exist_ok=True)
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("rank\tstatus\tpath\tnote\n")
        for r in rows:
            rank = r.get("rank","")
            status = r.get("status","")
            path = r.get("path","")
            note = r.get("note","")
            f.write(f"{rank}\t{status}\t{path}\t{note}\n")

def open_video(path: str):
    # start "" "<path>" — чтобы Windows открыл через ассоциацию
    subprocess.Popen(["cmd", "/c", "start", "", path], shell=False)

def main():
    files = sorted([p for p in READY_DIR.glob("*.mp4")])
    if not files:
        print("No mp4 in:", READY_DIR.resolve())
        return

    existing = load_tsv(TSV_PATH)

    # строим упорядоченный список строк (по текущим файлам)
    rows = []
    for i, p in enumerate(files, 1):
        ap = norm(p)
        prev = existing.get(ap, {})
        rows.append({
            "rank": str(i),
            "status": prev.get("status","").strip(),
            "path": ap,
            "note": prev.get("note","").strip(),
        })

    # helper: найти индекс первого без статуса
    def first_unreviewed():
        for idx, r in enumerate(rows):
            if r["status"] == "":
                return idx
        return 0  # если все заполнены — начнём сначала

    idx = first_unreviewed()

    print("Review file:", TSV_PATH.resolve())
    print("Videos:", len(rows))
    print("Commands: ok / no / later / skip / back / quit")
    print("Tip: можно дописать заметку после команды, напр: ok классное\n")

    while 0 <= idx < len(rows):
        r = rows[idx]
        print("\n" + "-"*80)
        print(f"[{idx+1}/{len(rows)}] status='{r['status']}'")
        print(r["path"])
        if r["note"]:
            print("note:", r["note"])

        # открываем видео
        open_video(r["path"])
        time.sleep(0.2)

        ans = input("-> ").strip()
        if not ans:
            continue

        cmd, *rest = ans.split(" ", 1)
        cmd = cmd.lower().strip()

        note = ""
        if rest:
            note = rest[0].strip()

        if cmd in {"quit","q"}:
            break
        if cmd == "back":
            idx = max(0, idx-1)
            continue
        if cmd == "skip":
            idx += 1
            continue
        if cmd not in {"ok","no","later"}:
            print("Unknown command. Use: ok / no / later / skip / back / quit")
            continue

        r["status"] = cmd
        if note:
            r["note"] = note

        # сохраняем сразу после каждого решения
        write_tsv(TSV_PATH, rows)
        print("saved:", cmd)

        idx += 1

    # финальная запись
    write_tsv(TSV_PATH, rows)
    print("\nDONE. Saved:", TSV_PATH.resolve())

if __name__ == "__main__":
    main()
