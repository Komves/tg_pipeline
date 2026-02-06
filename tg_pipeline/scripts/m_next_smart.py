import os, glob, json, random, shutil, datetime
import numpy as np
from collections import defaultdict, Counter

ROOT = os.path.abspath(".")
MIX  = os.path.join(ROOT, "data", "tg", "raw", "MIX")
TMP  = os.path.join(ROOT, "out", "tmp", "m_review_local")
LOGS = os.path.join(ROOT, "out", "logs")
CFG  = os.path.join(ROOT, "out", "config")
EMB  = os.path.join(ROOT, "out", "embeddings.npy")

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

BL_PATHS = os.path.join(CFG, "m_blacklist_paths.txt")
BL_PATS  = os.path.join(CFG, "m_blacklist_patterns.txt")

N = int(os.environ.get("N", "70"))
EXPLORE_FRAC = float(os.environ.get("EXPLORE_FRAC", "0.20"))

MIN_TOTAL = int(os.environ.get("MIN_TOTAL", "10"))
MIN_OKRATE = float(os.environ.get("MIN_OKRATE", "0.20"))
MIN_PER_CH = int(os.environ.get("MIN_PER_CH", "1"))
MAX_PER_CH = int(os.environ.get("MAX_PER_CH", "15"))

TASTE_WEIGHT = float(os.environ.get("TASTE_WEIGHT", "0.50"))
SUPER_BOOST = float(os.environ.get("SUPER_BOOST", "3.0"))  # SUPER сильнее OK

def read_lines(path):
    if not os.path.exists(path): return []
    out=[]
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            s=ln.strip()
            if not s or s.startswith("#"): continue
            out.append(s)
    return out

def load_seen_origs():
    seen=set()
    for p in sorted(glob.glob(os.path.join(LOGS, "m_feedback_*.tsv"))):
        with open(p, "r", encoding="utf-8") as f:
            hdr = f.readline()
            if "orig" not in hdr:
                continue
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    orig = parts[2].strip()
                    if orig:
                        seen.add(orig.lower())
    return seen

def channel_from_orig(orig):
    o = orig.replace("/", "\\")
    if "\\MIX\\" not in o:
        return "UNKNOWN"
    tail = o.split("\\MIX\\", 1)[1]
    return tail.split("\\", 1)[0] if "\\" in tail else tail

def build_channel_stats():
    stats = defaultdict(lambda: Counter())
    for p in sorted(glob.glob(os.path.join(LOGS, "m_feedback_*.tsv"))):
        with open(p, "r", encoding="utf-8") as f:
            hdr = f.readline()
            if "orig" not in hdr:
                continue
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                label = parts[1].strip().upper()
                orig  = parts[2].strip()
                if not orig or label not in ("OK","SUPER","NO","SKIP","BAN"):
                    continue
                ch = channel_from_orig(orig)
                stats[ch][label] += 1
                if label in ("OK","SUPER","NO","SKIP"):
                    stats[ch]["TOTAL"] += 1

    scored = []
    for ch, c in stats.items():
        total = int(c.get("TOTAL", 0))
        ok = float(c.get("OK", 0))
        sup = float(c.get("SUPER", 0))
        # SUPER усиливает ok
        ok_eff = ok + SUPER_BOOST * sup
        okrate = (ok_eff + 1.0) / (total + 2.0)  # smooth
        scored.append((ch, total, okrate))
    scored.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return scored

def list_all_images():
    out=[]
    for root, dirs, files in os.walk(MIX):
        for fn in files:
            if fn.lower().endswith(EXTS):
                out.append(os.path.join(root, fn))
    return out

def apply_blacklists(files, bl_paths, bl_pats, seen):
    out=[]
    bl_paths_l = set(p.lower() for p in bl_paths)
    bl_pats_l  = [p.lower() for p in bl_pats]
    for f in files:
        fl = f.lower()
        if fl in bl_paths_l:
            continue
        if any(pat in fl for pat in bl_pats_l):
            continue
        if fl in seen:
            continue
        out.append(f)
    return out

def weights_from_scored(scored):
    # scored: (ch, total, okrate)
    eligible = [(ch, total, okrate) for (ch, total, okrate) in scored
                if total >= MIN_TOTAL and okrate >= MIN_OKRATE and ch not in ("UNKNOWN","")]
    return {ch: okrate for (ch, total, okrate) in eligible}

def clean_tmp():
    os.makedirs(TMP, exist_ok=True)
    for fn in os.listdir(TMP):
        if fn in ("index.html", "data.js"):
            continue
        p = os.path.join(TMP, fn)
        if os.path.isfile(p):
            os.remove(p)

def write_data_js(items, batch_id):
    os.makedirs(TMP, exist_ok=True)
    data = f"window.BATCH_ID = {batch_id};\nwindow.BATCH_ITEMS = {json.dumps(items, ensure_ascii=False)};"
    with open(os.path.join(TMP, "data.js"), "w", encoding="utf-8") as f:
        f.write(data)

def load_embeddings():
    if not os.path.exists(EMB):
        return {}
    obj = np.load(EMB, allow_pickle=True)
    if hasattr(obj, "item"):
        return obj.item()
    return {}

def cosine(a, b):
    na = np.linalg.norm(a); nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

def build_taste_centroids(embeddings):
    ok_vecs=[]
    no_vecs=[]
    for p in sorted(glob.glob(os.path.join(LOGS, "m_feedback_*.tsv"))):
        with open(p, "r", encoding="utf-8") as f:
            hdr = f.readline()
            if "orig" not in hdr:
                continue
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                label = parts[1].strip().upper()
                orig  = parts[2].strip()
                if not orig or orig not in embeddings:
                    continue
                if label == "OK":
                    ok_vecs.append(embeddings[orig])
                elif label == "SUPER":
                    # SUPER кладем несколько раз (усиление)
                    for _ in range(int(round(SUPER_BOOST))):
                        ok_vecs.append(embeddings[orig])
                elif label == "NO":
                    no_vecs.append(embeddings[orig])
    ok_c = np.mean(ok_vecs, axis=0) if ok_vecs else None
    no_c = np.mean(no_vecs, axis=0) if no_vecs else None
    return ok_c, no_c

def taste_score(orig, embeddings, ok_c, no_c):
    if not embeddings or ok_c is None or orig not in embeddings:
        return 0.0
    v = embeddings[orig]
    s_ok = cosine(v, ok_c)
    s_no = cosine(v, no_c) if no_c is not None else 0.0
    return s_ok - s_no

def main():
    clean_tmp()

    seen = load_seen_origs()
    bl_paths = read_lines(BL_PATHS)
    bl_pats  = read_lines(BL_PATS)

    all_imgs = list_all_images()
    pool = apply_blacklists(all_imgs, bl_paths, bl_pats, seen)
    if not pool:
        print("POOL EMPTY after filters.")
        return

    # channel weights (SUPER-aware)
    ch_scored = build_channel_stats()
    ch_w = weights_from_scored(ch_scored)

    # taste (optional; if no embeddings.npy -> still works by channels)
    emb = load_embeddings()
    ok_c, no_c = build_taste_centroids(emb) if emb else (None, None)

    # score each file
    scored_files=[]
    for f in pool:
        ch = channel_from_orig(f)
        w_channel = ch_w.get(ch, 0.0)
        w_taste = taste_score(f, emb, ok_c, no_c)
        score = (1.0 - TASTE_WEIGHT) * w_channel + TASTE_WEIGHT * w_taste
        scored_files.append((score, f))
    scored_files.sort(key=lambda x: x[0], reverse=True)

    explore_n = int(round(N * EXPLORE_FRAC))
    exploit_n = max(0, N - explore_n)

    picked = [f for (_, f) in scored_files[:min(exploit_n, len(scored_files))]]
    remaining = [f for (_, f) in scored_files[min(exploit_n, len(scored_files)):]]
    random.shuffle(remaining)
    picked.extend(remaining[:min(explore_n, len(remaining))])

    random.shuffle(picked)
    picked = picked[:min(N, len(picked))]

    items=[]
    for idx, orig in enumerate(picked, start=1):
        ext = os.path.splitext(orig)[1].lower()
        shown = f"m_{idx:03d}{ext}"
        shutil.copy2(orig, os.path.join(TMP, shown))
        items.append({"src": shown, "orig": orig, "label": ""})

    batch_id = int(datetime.datetime.utcnow().timestamp())
    write_data_js(items, batch_id)

    print(f"SMART+SUPER READY: {len(items)} (batch_id={batch_id}) explore={explore_n} exploit={exploit_n} super_boost={SUPER_BOOST}")
    os.system(f'start "" "http://127.0.0.1:8765/index.html?v={batch_id}"')

if __name__ == "__main__":
    main()
