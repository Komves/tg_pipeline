import os, json, time, subprocess, tempfile, shutil, html
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

IN_DIR = Path(r".\data\tg\raw\MIX")
PROFILE = Path(r".\out\b_profile.npy")

OUT_JSONL = Path(r".\out\reports\b_rank_mix_fast_live.jsonl")
OUT_HTML  = Path(r".\out\reports\b_top_mix_fast_live.html")

THRESH = 0.92
TOP_N = 200
BATCH = 32
FFMPEG_TIMEOUT = 3
SEEK_PRIMARY = 1.0
SEEK_FALLBACK = 0.0
MIN_SIZE_BYTES = 1 * 1024 * 1024

PROGRESS_EVERY = 300

def run(cmd, timeout=None):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    p = run(
        ["ffmpeg","-hide_banner","-loglevel","error",
         "-threads","1",
         "-an","-sn",                # ускорение: без аудио/сабов
         "-ss", str(max(0.0,t)),
         "-i", video_path,
         "-frames:v","1",
         "-q:v","2",
         out_jpg],
        timeout=FFMPEG_TIMEOUT
    )
    return (p is not None and p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0)

def load_rgb(p: str) -> Image.Image:
    return Image.open(p).convert("RGB")

def cosine_rows(mat: np.ndarray, v: np.ndarray) -> np.ndarray:
    return (mat @ v).astype(np.float32)

def main():
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    profile = np.load(str(PROFILE)).astype(np.float32)
    profile = profile / (np.linalg.norm(profile) + 1e-12)

    vids = sorted(IN_DIR.rglob("*.mp4"))
    vids2 = []
    for p in vids:
        try:
            if p.stat().st_size >= MIN_SIZE_BYTES:
                vids2.append(p)
        except:
            pass
    vids = vids2

    # resume: уже обработанные пути
    done = set()
    if OUT_JSONL.exists():
        with open(OUT_JSONL, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["path"])
                except:
                    pass

    todo = [p for p in vids if str(p) not in done]

    print("[rank-fast-live] in_dir:", IN_DIR.resolve())
    print("[rank-fast-live] total (>=1MB):", len(vids))
    print("[rank-fast-live] already_done:", len(done))
    print("[rank-fast-live] todo:", len(todo))
    print("[rank-fast-live] thresh:", THRESH, "batch:", BATCH)

    device="cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="brank_fast_"))
    timeout_or_fail = 0
    skipped_img = 0

    batch_tensors = []
    batch_paths = []

    t0 = time.time()
    processed = 0

    # append mode for live logging
    f_out = open(OUT_JSONL, "a", encoding="utf-8")
    try:
        for vp in todo:
            try:
                Path(r".\\out\\logs").mkdir(parents=True, exist_ok=True)
                Path(r".\\out\\logs\\rank_fast_current.txt").write_text(str(vp), encoding="utf-8")
            except:
                pass

            out_jpg = str(tmpdir / f"{vp.stem}.jpg")
            ok = extract_frame(str(vp), SEEK_PRIMARY, out_jpg)
            if not ok:
                ok = extract_frame(str(vp), SEEK_FALLBACK, out_jpg)
            if not ok:
                timeout_or_fail += 1
                processed += 1
                continue

            try:
                ten = preprocess(load_rgb(out_jpg)).unsqueeze(0)
            except:
                skipped_img += 1
                processed += 1
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
                    f_out.write(json.dumps({"path": pth, "score": float(sc)}, ensure_ascii=False) + "\n")
                f_out.flush()

                batch_tensors.clear()
                batch_paths.clear()

            processed += 1
            if processed % PROGRESS_EVERY == 0:
                done_now = len(done) + processed
                left = len(vids) - done_now
                print(f"[progress] done={done_now}/{len(vids)} left={left} fail={timeout_or_fail} skipped={skipped_img}")

        # flush остаток
        if batch_tensors:
            batch = torch.cat(batch_tensors, dim=0)
            with torch.no_grad():
                feats = model.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            feats_np = feats.cpu().numpy().astype(np.float32)
            sims = cosine_rows(feats_np, profile)
            for pth, sc in zip(batch_paths, sims):
                f_out.write(json.dumps({"path": pth, "score": float(sc)}, ensure_ascii=False) + "\n")
            f_out.flush()

    finally:
        f_out.close()
        shutil.rmtree(tmpdir, ignore_errors=True)

    # соберём top для html из уже записанного jsonl
    rows = []
    with open(OUT_JSONL, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except:
                pass
    rows.sort(key=lambda r: r["score"], reverse=True)
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
<title>B rank MIX FAST LIVE</title>
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 10px; }}
.card {{ background:#1c1c1c; padding:10px; border-radius:10px; }}
a {{ color:#4ea3ff; text-decoration:none; font-size:13px; }}
.score {{ font-size:12px; color:#aaa; margin-bottom:6px; }}
.path {{ font-size:11px; color:#888; margin-top:6px; word-break: break-all; }}
</style></head><body>
<h2>B rank MIX FAST LIVE (score ≥ {THRESH})</h2>
<div>total lines: {len(rows)} | kept: {len(top)} | fail(frame): {timeout_or_fail} | skipped(img): {skipped_img}</div>
<div class="grid">{''.join(cards)}</div>
</body></html>""")

    print("\n[done]")
    print("lines:", len(rows))
    print("kept>=thresh:", len(top))
    print("fail_frame:", timeout_or_fail)
    print("skipped_img:", skipped_img)
    print("seconds:", round(time.time()-t0, 1))
    print("jsonl:", OUT_JSONL.resolve())
    print("html :", OUT_HTML.resolve())

if __name__ == "__main__":
    main()
