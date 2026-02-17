import json, os, time, tempfile, shutil, subprocess
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

def load_cfg(root: Path) -> dict:
    p = root / "out" / "config" / "engine.local.json"
    return json.loads(p.read_text(encoding="utf-8"))

def extract_frame(video_path: str, t: float, out_jpg: str, timeout=3) -> bool:
    try:
        p = subprocess.run(
            ["ffmpeg","-hide_banner","-loglevel","error",
             "-threads","1","-an","-sn",
             "-ss", str(max(0.0,t)),
             "-i", video_path,
             "-frames:v","1","-q:v","2", out_jpg],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
        )
        return p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0
    except Exception:
        return False

def best_embed(model, preprocess, prof, vp: Path, tmpdir: Path, seek_times=(0.5,1.5,2.5)) -> np.ndarray | None:
    best = None
    best_v = None
    for t in seek_times:
        jpg = tmpdir / f"f_{int(time.time()*1000)}_{int(t*10)}.jpg"
        if not extract_frame(str(vp), t, str(jpg)):
            continue
        try:
            img = Image.open(jpg).convert("RGB")
            ten = preprocess(img).unsqueeze(0)
            with torch.no_grad():
                feat = model.encode_image(ten)
                feat = feat / feat.norm(dim=-1, keepdim=True)
            v = feat.squeeze(0).cpu().numpy().astype(np.float32)
            score = float(v @ prof)
            if best is None or score > best:
                best = score
                best_v = v
        except Exception:
            pass
    return best_v

def main():
    root = Path(__file__).resolve().parent
    cfg = load_cfg(root)

    out_dir = Path(cfg["out_dir"])
    logs_dir = Path(cfg["logs_dir"])
    idx_dir  = Path(cfg["index_dir"])

    profile_path = Path(cfg["profile_b"])
    seeds_path   = Path(cfg["seeds_b"])
    fb_path      = logs_dir / "b_feedback.tsv"
    applied_path = idx_dir / "b_feedback_applied_lines.txt"

    if not fb_path.exists():
        print("[taste] no feedback file yet:", fb_path)
        return

    # read feedback lines (skip header)
    lines = fb_path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= 1:
        print("[taste] feedback empty")
        return
    rows = lines[1:]

    applied = 0
    if applied_path.exists():
        try:
            applied = int(applied_path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            applied = 0

    new_rows = rows[applied:]
    if not new_rows:
        print("[taste] nothing new to apply")
        return

    # parse, keep only OK
    ok_paths = []
    for r in new_rows:
        parts = r.split("\t")
        if len(parts) < 4: 
            continue
        _, label, _, path = parts[0], parts[1], parts[2], parts[3]
        if label.strip().upper() == "OK":
            ok_paths.append(Path(path))

    if not ok_paths:
        print("[taste] no OK rows in new feedback (only NO/skip)")
        applied_path.write_text(str(len(rows)), encoding="utf-8")
        return

    if not profile_path.exists():
        raise SystemExit("[taste] missing profile: " + str(profile_path))

    prof = np.load(str(profile_path)).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)

    # estimate old count from seeds file (fallback 1)
    old_n = 1
    if seeds_path.exists():
        try:
            old_n = max(1, len([x for x in seeds_path.read_text(encoding="utf-8").splitlines() if x.strip()]))
        except Exception:
            old_n = 1

    # model
    try:
        torch.set_num_threads(1)
    except Exception:
        pass

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device="cpu")
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="taste_b_"))
    vecs = []
    bad = 0

    try:
        for vp in ok_paths:
            v = best_embed(model, preprocess, prof, vp, tmpdir)
            if v is None:
                bad += 1
                continue
            v = v / (np.linalg.norm(v) + 1e-12)
            vecs.append(v)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not vecs:
        print("[taste] no embeddings extracted from OK videos; bad=", bad)
        applied_path.write_text(str(len(rows)), encoding="utf-8")
        return

    mean_ok = np.mean(np.stack(vecs, axis=0), axis=0).astype(np.float32)
    mean_ok = mean_ok / (np.linalg.norm(mean_ok) + 1e-12)

    # incremental mean: weight by old_n
    new_n = old_n + len(vecs)
    new_prof = (prof * old_n + mean_ok * len(vecs)) / float(new_n)
    new_prof = new_prof.astype(np.float32)
    new_prof = new_prof / (np.linalg.norm(new_prof) + 1e-12)

    # backup + save
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = profile_path.with_suffix(profile_path.suffix + f".bak_{stamp}")
    shutil.copy2(profile_path, bak)
    np.save(str(profile_path), new_prof)

    # append OK paths to seeds history
    seeds_path.parent.mkdir(parents=True, exist_ok=True)
    with open(seeds_path, "a", encoding="utf-8") as f:
        for vp in ok_paths:
            f.write(str(vp) + "\n")

    # mark applied
    applied_path.write_text(str(len(rows)), encoding="utf-8")

    print("[taste] applied OK:", len(vecs), "bad:", bad)
    print("[taste] old_n:", old_n, "new_n:", new_n)
    print("[taste] profile updated:", profile_path)
    print("[taste] backup:", bak)
    print("[taste] applied cursor:", applied_path)

if __name__ == "__main__":
    main()
