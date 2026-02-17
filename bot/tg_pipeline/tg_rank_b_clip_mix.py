import os, json, time, subprocess, tempfile, shutil, html
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

IN_DIR = Path(r".\data\tg\raw\MIX")
PROFILE = Path(r".\out\b_profile.npy")

OUT_JSONL = Path(r".\out\reports\b_rank_mix.jsonl")
OUT_HTML  = Path(r".\out\reports\b_top_mix.html")

FRAMES_PER_VIDEO = 3
TOP_N = 120
THRESH = 0.92  # стартуем строго (раз ты сказал "все B" в топе)
MAX_FILES = 0  # 0 = все

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def ffprobe_duration(video_path: str) -> float:
    cmd = ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", video_path]
    p = run(cmd)
    if p.returncode != 0:
        return float("nan")
    try:
        return float(p.stdout.strip())
    except:
        return float("nan")

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-ss", str(max(0.0,t)),"-i", video_path, "-frames:v","1","-q:v","2", out_jpg]
    p = run(cmd)
    return p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0

def load_rgb(p: str) -> Image.Image:
    return Image.open(p).convert("RGB")

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def main():
    profile = np.load(str(PROFILE)).astype(np.float32)
    profile = profile / (np.linalg.norm(profile) + 1e-12)

    vids = sorted(IN_DIR.rglob("*.mp4"))
    if MAX_FILES and len(vids) > MAX_FILES:
        vids = vids[:MAX_FILES]

    print("[rank] in_dir:", IN_DIR.resolve())
    print("[rank] videos:", len(vids))
    print("[rank] thresh:", THRESH)

    device="cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="brank_mix_"))
    rows = []
    kept = 0
    t0 = time.time()
    try:
        for i, vp in enumerate(vids, 1):
            dur = ffprobe_duration(str(vp))
            if not (dur and dur == dur) or dur <= 0.2:
                ts = [0.0]
            else:
                ts = [dur*0.10, dur*0.50, dur*0.90][:FRAMES_PER_VIDEO]

            frame_paths = []
            for j, t in enumerate(ts):
                fp = tmpdir / f"{vp.stem}_{j}.jpg"
                if extract_frame(str(vp), t, str(fp)):
                    frame_paths.append(str(fp))
            if not frame_paths:
                continue

            imgs = []
            for fp in frame_paths:
                try:
                    imgs.append(preprocess(load_rgb(fp)).unsqueeze(0))
                except:
                    pass
            if not imgs:
                continue

            batch = torch.cat(imgs, dim=0)
            with torch.no_grad():
                feats = model.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            feats_np = feats.cpu().numpy()

            score = float(max(cosine(v, profile) for v in feats_np))
            rec = {"path": str(vp), "score": score, "duration_s": float(dur) if (dur and dur==dur) else None}

            rows.append(rec)
            if score >= THRESH:
                kept += 1

            if i % 200 == 0:
                print(f"[progress] {i}/{len(vids)} scored, kept(>=thresh)={kept}")

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

    html_doc = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>B rank MIX TOP</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px; }}
.card {{ background:#1c1c1c; padding:10px; border-radius:10px; }}
a {{ color:#4ea3ff; text-decoration:none; font-size:13px; }}
.score {{ font-size:12px; color:#aaa; margin-bottom:6px; }}
.path {{ font-size:11px; color:#888; margin-top:6px; word-break: break-all; }}
</style></head>
<body>
<h2>B rank MIX (score ≥ {THRESH})</h2>
<div>files scored: {len(rows)} | kept: {len(top)} | generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
<div class="grid">{''.join(cards)}</div>
</body></html>"""

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_doc)

    dt = time.time() - t0
    print("\n[done]")
    print("scored:", len(rows))
    print("kept>=thresh:", len(top))
    print("seconds:", round(dt, 1))
    print("jsonl:", str(OUT_JSONL.resolve()))
    print("html :", str(OUT_HTML.resolve()))
    print("\nTOP10 overall:")
    for r in rows[:10]:
        print(f"{r['score']:.4f}  {r['path']}")

if __name__ == "__main__":
    main()
