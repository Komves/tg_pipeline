import os, json, time, subprocess, tempfile, shutil, html
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

IN_DIR = Path(r".\data\tg\raw\MIX")
PROFILE = Path(r".\out\b_profile.npy")

OUT_JSONL = Path(r".\out\reports\b_rank_mix_fast.jsonl")
OUT_HTML  = Path(r".\out\reports\b_top_mix_fast.html")

# FAST SETTINGS
THRESH = 0.92
TOP_N = 200
BATCH = 32
FFMPEG_TIMEOUT = 12

# 1 кадр: пробуем на 1.0с, если не вышло — на 0.0с
SEEK_PRIMARY = 1.0
SEEK_FALLBACK = 0.0

# грубый отсев совсем мелких файлов (ускоряет и уменьшает мусор)
MIN_SIZE_BYTES = 1 * 1024 * 1024  # 1MB

def run(cmd, timeout=None):
    try:
        return subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    # -ss BEFORE -i быстрее
    p = run(
        ["ffmpeg","-hide_banner","-loglevel","error",
         "-ss", str(max(0.0,t)),
         "-i", video_path,
         "-frames:v","1",
         "-q:v","2",
         out_jpg],
        timeout=FFMPEG_TIMEOUT
    )
    return (
        p is not None and
        p.returncode == 0 and
        os.path.exists(out_jpg) and
        os.path.getsize(out_jpg) > 0
    )

def load_rgb(p: str) -> Image.Image:
    return Image.open(p).convert("RGB")

def cosine_rows(mat: np.ndarray, v: np.ndarray) -> np.ndarray:
    # mat: (N,D) normalized, v: (D,) normalized
    return (mat @ v).astype(np.float32)

def main():
    if not PROFILE.exists():
        raise SystemExit(f"Missing profile: {PROFILE}")

    profile = np.load(str(PROFILE)).astype(np.float32)
    profile = profile / (np.linalg.norm(profile) + 1e-12)

    vids = sorted(IN_DIR.rglob("*.mp4"))
    # size prefilter
    vids2 = []
    for p in vids:
        try:
            if p.stat().st_size >= MIN_SIZE_BYTES:
                vids2.append(p)
        except:
            pass
    vids = vids2

    print("[rank-fast] in_dir:", IN_DIR.resolve())
    print("[rank-fast] videos (>=1MB):", len(vids))
    print("[rank-fast] thresh:", THRESH, "batch:", BATCH)

    device="cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="brank_fast_"))
    rows = []
    skipped = 0
    timeout_or_fail = 0

    batch_tensors = []
    batch_paths = []

    t0 = time.time()
    try:
        for i, vp in enumerate(vids, 1):
            out_jpg = str(tmpdir / f"{vp.stem}.jpg")

            ok = extract_frame(str(vp), SEEK_PRIMARY, out_jpg)
            if not ok:
                ok = extract_frame(str(vp), SEEK_FALLBACK, out_jpg)

            if not ok:
                timeout_or_fail += 1
                continue

            try:
                img = load_rgb(out_jpg)
                ten = preprocess(img).unsqueeze(0)
            except:
                skipped += 1
                continue

            batch_tensors.append(ten)
            batch_paths.append(str(vp))

            if len(batch_tensors) >= BATCH:
                batch = torch.cat(batch_tensors, dim=0)
                with torch.no_grad():
                    feats = model.encode_image(batch)
                    feats = feats / feats.norm(dim=-1, keepdim=True)
                feats_np = feats.cpu().numpy().astype(np.float32)
                sims = cosine_rows(feats_np, profile)
                for pth, sc in zip(batch_paths, sims):
                    rows.append({"path": pth, "score": float(sc)})
                batch_tensors.clear()
                batch_paths.clear()

            if i % 300 == 0:
                print(f"[progress] {i}/{len(vids)} | scored={len(rows)} | fail={timeout_or_fail} | skipped={skipped}")

        # flush last batch
        if batch_tensors:
            batch = torch.cat(batch_tensors, dim=0)
            with torch.no_grad():
                feats = model.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            feats_np = feats.cpu().numpy().astype(np.float32)
            sims = cosine_rows(feats_np, profile)
            for pth, sc in zip(batch_paths, sims):
                rows.append({"path": pth, "score": float(sc)})

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    rows.sort(key=lambda r: r["score"], reverse=True)

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    top = [r for r in rows if r["score"] >= THRESH][:TOP_N]

    cards = []
    for r in top:
        p = r["path"].replace("\\", "/")
        url = "file:///" + p
        cards.append(f"""
        <div class="card">
          <div class="score">score: {r['score']:.4f}</div>
          <a href="{html.escape(url)}">▶ Открыть видео</a>
          <div class="path">{html.escape(r['path'])}</div>
        </div>
        """)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>B rank MIX FAST</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 10px; }}
.card {{ background:#1c1c1c; padding:10px; border-radius:10px; }}
a {{ color:#4ea3ff; text-decoration:none; font-size:13px; }}
.score {{ font-size:12px; color:#aaa; margin-bottom:6px; }}
.path {{ font-size:11px; color:#888; margin-top:6px; word-break: break-all; }}
</style></head><body>
<h2>B rank MIX FAST (score ≥ {THRESH})</h2>
<div>scored: {len(rows)} | kept: {len(top)} | fail(frame): {timeout_or_fail} | skipped(img): {skipped}</div>
<div class="grid">{''.join(cards)}</div>
</body></html>""")

    print("\n[done]")
    print("scored:", len(rows))
    print("kept>=thresh:", len(top))
    print("fail_frame:", timeout_or_fail)
    print("skipped_img:", skipped)
    print("seconds:", round(time.time()-t0, 1))
    print("jsonl:", OUT_JSONL.resolve())
    print("html :", OUT_HTML.resolve())
    print("\nTOP10:")
    for r in rows[:10]:
        print(f"{r['score']:.4f}  {r['path']}")

if __name__ == "__main__":
    main()
