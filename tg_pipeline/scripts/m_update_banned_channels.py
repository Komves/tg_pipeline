import os, re
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

FEEDBACK = Path("out/logs/a_feedback_master.tsv")
OUT = Path("out/logs/a_meme_banned_channels.tsv")

WINDOW = int(os.environ.get("A_CH_BAN_WINDOW", "30"))
THRESH = int(os.environ.get("A_CH_BAN_THRESH", "3"))
TTL_DAYS = int(os.environ.get("A_CH_BAN_TTL_DAYS", "7"))

def extract_channel(p: str):
    p = p.replace("/", "\\")
    m = re.search(r"\\data\\tg\\raw\\MIX\\([^\\]+)\\", p, flags=re.I)
    return m.group(1) if m else None

def load_existing():
    m = {}
    if not OUT.exists():
        return m
    for line in OUT.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split("\t")
        if len(parts) != 2: 
            continue
        ch, until = parts
        m[ch] = until
    return m

def main():
    if not FEEDBACK.exists():
        print("[M_BAN_CH] no feedback"); return

    per_ch = defaultdict(lambda: deque(maxlen=WINDOW))
    for line in FEEDBACK.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
        parts = line.split("\t", 3)
        if len(parts) < 4: 
            continue
        _ts, label, _score, path = parts
        ch = extract_channel(path)
        if not ch:
            continue
        per_ch[ch].append(label.strip().upper())

    now = datetime.utcnow()
    ban_until = (now + timedelta(days=TTL_DAYS)).isoformat(timespec="seconds") + "Z"

    existing = load_existing()
    to_ban = set()
    for ch, dq in per_ch.items():
        ban_count = sum(1 for x in dq if x == "BAN")
        if ban_count >= THRESH:
            to_ban.add(ch)

    # обновляем/добавляем только тех, кто сейчас триггернулся
    for ch in to_ban:
        existing[ch] = ban_until

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{ch}\t{existing[ch]}" for ch in sorted(existing)]
    OUT.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    print(f"[M_BAN_CH] window={WINDOW} thresh={THRESH} ttl_days={TTL_DAYS} banned_now={len(to_ban)} total={len(existing)} -> {OUT}")

if __name__ == "__main__":
    main()
