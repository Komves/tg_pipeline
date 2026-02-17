import os, time, json, re
from pathlib import Path

MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))
CUT = time.time() - MAX_AGE_HOURS * 3600
BLOCK_RE = re.compile(os.environ.get("A_MEME_BLOCK_RE", r"$^"), re.I)

DATA = Path("out/tmp/m_review_local/data.js")
FEEDBACK = Path("out/logs/a_feedback_master.tsv")
BANNED_CH_FILE = Path("out/logs/a_meme_banned_channels.txt")   # канал-бан (пороговый)
BANNED_ITEM_FILE = Path("out/logs/a_meme_banned_items.txt")    # точечный бан

def channel_of(orig: str):
    orig = orig.replace("/", "\\")
    m = re.search(r"\\data\\tg\\raw\\MIX\\([^\\]+)\\", orig, flags=re.I)
    return m.group(1) if m else ""

def load_seen():
    s=set()
    if FEEDBACK.exists():
        for line in FEEDBACK.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            parts=line.split("\t",3)
            if len(parts)==4:
                s.add(parts[3].strip())
    return s

def load_list(p: Path):
    if not p.exists():
        return set()
    return set(x.strip() for x in p.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip())

def parse_items(txt: str):
    m_id = re.search(r"window\.BATCH_ID\s*=\s*([0-9]+);", txt)
    m_it = re.search(r"window\.BATCH_ITEMS\s*=\s*(\[\{.*\}\]);", txt, flags=re.DOTALL)
    if not (m_id and m_it):
        raise SystemExit("[M_FIX] cannot parse data.js")
    return m_id.group(1), json.loads(m_it.group(1))

def run_filter(items, banned_ch_enabled: bool):
    banned_item = load_list(BANNED_ITEM_FILE)
    banned_ch = load_list(BANNED_CH_FILE) if banned_ch_enabled else set()
    seen = load_seen()

    kept=[]
    kept_orig=set()
    d_old=d_block=d_seen=d_dupe=d_bitem=d_bch=d_missing=0

    for it in items:
        orig=(it.get("orig","") or "").strip()
        if not orig:
            d_missing += 1; continue

        if orig in banned_item:
            d_bitem += 1; continue

        if BLOCK_RE.search(orig):
            d_block += 1; continue

        ch = channel_of(orig)
        if banned_ch_enabled and ch and ch in banned_ch:
            d_bch += 1; continue

        if orig in seen:
            d_seen += 1; continue

        p = Path(orig)
        if not p.exists():
            d_missing += 1; continue

        if p.stat().st_mtime < CUT:
            d_old += 1; continue

        if orig in kept_orig:
            d_dupe += 1; continue

        kept.append(it); kept_orig.add(orig)

    return kept, dict(
        dropped_old=d_old, dropped_block=d_block, dropped_seen=d_seen, dropped_dupe=d_dupe,
        dropped_banned_item=d_bitem, dropped_banned_ch=d_bch, dropped_missing=d_missing,
        banned_ch_enabled=banned_ch_enabled
    )

txt = DATA.read_text(encoding="utf-8", errors="ignore")
batch_id, items = parse_items(txt)

kept, st = run_filter(items, banned_ch_enabled=True)

# авто-fallback: если получилось 0 и канал-бан что-то выкинул — отключаем канал-бан
if len(kept)==0 and st["dropped_banned_ch"]>0:
    kept2, st2 = run_filter(items, banned_ch_enabled=False)
    print("[M_FIX] fallback: disable channel bans -> kept=", len(kept2))
    kept, st = kept2, st2

out = f"window.BATCH_ID = {batch_id};\n"
out += "window.BATCH_ITEMS = " + json.dumps(kept, ensure_ascii=False) + ";"
DATA.write_text(out, encoding="utf-8")

print(f"[M_FIX] kept={len(kept)} dropped_old={st['dropped_old']} dropped_block={st['dropped_block']} dropped_seen={st['dropped_seen']} dropped_dupe={st['dropped_dupe']} dropped_banned_item={st['dropped_banned_item']} dropped_banned_ch={st['dropped_banned_ch']} dropped_missing={st['dropped_missing']} max_age_h={MAX_AGE_HOURS} ch_ban={st['banned_ch_enabled']}")
