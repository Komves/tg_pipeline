import json, os, subprocess, sys
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent

RANK = ROOT / "out" / "reports" / "a_rank_memes.jsonl"
OUT  = ROOT / "out" / "logs" / "a_feedback.tsv"

TOPN = 70

def load_items(rank_path: Path, topn: int):
    items=[]
    # jsonl may be big; keep only ok rows with score
    with rank_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line=line.strip()
            if not line: 
                continue
            try:
                o=json.loads(line)
            except:
                continue
            if o.get("status")=="ok" and o.get("path") and o.get("score") is not None:
                p=str(o["path"])
                if os.path.exists(p):
                    items.append((float(o["score"]), p))
    items.sort(key=lambda x: x[0], reverse=True)
    return items[:topn]

def ensure_out():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not OUT.exists():
        OUT.write_text("ts\tlabel\tscore\tpath\n", encoding="utf-8")

def append_row(label: str, score: float, path: str):
    ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with OUT.open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{label}\t{score:.4f}\t{path}\n")

def open_video(path: str):
    # Use default Windows app. Non-blocking.
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except Exception:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", path], shell=False)
            return True
        except Exception:
            return False

class App:
    def __init__(self, items):
        self.items = items
        self.i = 0
        self.cur_opened = False

        self.root = tk.Tk()
        self.root.title("A Offline Review (OK/NO)")
        self.root.geometry("620x260")
        self.root.attributes("-topmost", True)

        self.lbl = tk.Label(self.root, text="", font=("Segoe UI", 12), justify="left", wraplength=600)
        self.lbl.pack(pady=10)

        btns = tk.Frame(self.root)
        btns.pack(pady=8)

        self.btn_ok = tk.Button(btns, text="✅ OK", font=("Segoe UI", 16), width=10, command=self.mark_ok)
        self.btn_no = tk.Button(btns, text="❌ NO", font=("Segoe UI", 16), width=10, command=self.mark_no)
        self.btn_sk = tk.Button(btns, text="⏭ SKIP", font=("Segoe UI", 16), width=10, command=self.mark_skip)

        self.btn_ok.grid(row=0, column=0, padx=8)
        self.btn_no.grid(row=0, column=1, padx=8)
        self.btn_sk.grid(row=0, column=2, padx=8)

        bottom = tk.Frame(self.root)
        bottom.pack(pady=10)

        self.btn_open = tk.Button(bottom, text="▶ Open video", font=("Segoe UI", 11), command=self.open_current)
        self.btn_quit = tk.Button(bottom, text="Quit", font=("Segoe UI", 11), command=self.quit)

        self.btn_open.grid(row=0, column=0, padx=8)
        self.btn_quit.grid(row=0, column=1, padx=8)

        self.root.bind("<KeyPress-o>", lambda e: self.mark_ok())
        self.root.bind("<KeyPress-n>", lambda e: self.mark_no())
        self.root.bind("<KeyPress-s>", lambda e: self.mark_skip())
        self.root.bind("<KeyPress-Right>", lambda e: self.mark_skip())
        self.root.bind("<KeyPress-space>", lambda e: self.open_current())

        self.render()

    def render(self):
        if self.i >= len(self.items):
            self.lbl.config(text=f"Done. Saved to:\n{OUT}\n\nOK/NO are recorded.\nYou can close this window.")
            self.btn_ok.config(state="disabled")
            self.btn_no.config(state="disabled")
            self.btn_sk.config(state="disabled")
            self.btn_open.config(state="disabled")
            return

        score, path = self.items[self.i]
        tail = path[-120:] if len(path) > 120 else path
        self.lbl.config(text=f"#{self.i+1}/{len(self.items)}  score={score:.4f}\n\n{tail}\n\nKeys: O=OK  N=NO  S=SKIP  Space=Open")
        self.cur_opened = False

    def open_current(self):
        if self.i >= len(self.items):
            return
        score, path = self.items[self.i]
        ok = open_video(path)
        if not ok:
            messagebox.showerror("Error", "Could not open video with default player.")
        else:
            self.cur_opened = True

    def mark_ok(self):
        if self.i >= len(self.items):
            return
        score, path = self.items[self.i]
        append_row("OK", score, path)
        self.i += 1
        self.render()

    def mark_no(self):
        if self.i >= len(self.items):
            return
        score, path = self.items[self.i]
        append_row("NO", score, path)
        self.i += 1
        self.render()

    def mark_skip(self):
        if self.i >= len(self.items):
            return
        self.i += 1
        self.render()

    def quit(self):
        self.root.destroy()

def main():
    if not RANK.exists():
        print("missing:", RANK)
        sys.exit(1)
    ensure_out()
    items = load_items(RANK, TOPN)
    if not items:
        print("no items found in:", RANK)
        sys.exit(2)
    print(f"[A-review] items={len(items)}  rank={RANK}")
    print(f"[A-review] output={OUT}")
    app = App(items)
    app.root.mainloop()

if __name__ == "__main__":
    main()
