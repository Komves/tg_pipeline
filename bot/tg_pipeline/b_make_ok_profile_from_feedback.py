import os, re, subprocess, time
from pathlib import Path
import numpy as np
from PIL import Image
import open_clip
import torch

ROOT = Path(__file__).resolve().parent
FEED = ROOT / r"out\logs\b_feedback.tsv"
OUTP = ROOT / r"out\b_profile_ok.npy"

N_OK_MAX = 200          # сколько ok использовать максимум
FRAMES_PER_VIDEO = 3    # сколько кадров на видео
SEEK_TIMES = [0.5, 1.5, 2.5]
FFMPEG_TIMEOUT = 4

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    try:
        p = subprocess.run(
            ["ffmpeg","-hide_banner","-loglevel","error",
             "-threads","1","-an","-sn",
             "-ss", str(max(0.0,t)),
             "-i", video_path,
             "-frames:v","1","-q:v","2", out_jpg],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=FFMPEG_TIMEOUT
        )
        return p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0
    except subprocess.TimeoutExpired:
        return False

def main():
    if not FEED.exists():
        raise SystemExit("missing feedback: " + str(FEED))

    ok_paths = []
    for line in FEED.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[2].strip() == "ok":
            ok_paths.append(parts[3].strip())

    # берём последние N_OK_MAX (самые свежие клики важнее)
    ok_paths = [p for p in ok_paths if p]
    ok_paths = ok_paths[-N_OK_MAX:]

    # оставим только существующие
    ok_paths = [p for p in ok_paths if os.path.exists(p)]
    if not ok_paths:
        raise SystemExit("no ok paths exist on disk")

    print("[ok_profile] ok_videos:", len(ok_paths))

    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    feats = []
    frames_used = 0
    videos_used = 0
    videos_failed = 0

    tmp = ROOT / "out" / "tmp_ok_profile"
    tmp.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    for idx, vp in enumerate(ok_paths, 1):
        got_any = False
        for k in range(FRAMES_PER_VIDEO):
            t = SEEK_TIMES[k] if k < len(SEEK_TIMES) else 1.0 + k
            jpg = tmp / f"ok_{idx}_{k}.jpg"
            if not extract_frame(vp, t, str(jpg)):
                continue
            try:
                img = Image.open(jpg).convert("RGB")
                ten = preprocess(img).unsqueeze(0)
                with torch.no_grad():
                    f = model.encode_image(ten)
                    f = f / f.norm(dim=-1, keepdim=True)
                feats.append(f.squeeze(0).cpu().numpy().astype(np.float32))
                frames_used += 1
                got_any = True
            except:
                pass
        if got_any:
            videos_used += 1
        else:
            videos_failed += 1

        if idx % 10 == 0:
            print(f"[progress] {idx}/{len(ok_paths)} videos_used={videos_used} frames_used={frames_used} failed={videos_failed}")

    if not feats:
        raise SystemExit("no frames embedded")

    prof = np.mean(np.stack(feats, axis=0), axis=0).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)

    OUTP.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(OUTP), prof)

    print("\n[done]")
    print("videos_used:", videos_used, "videos_failed:", videos_failed, "frames_used:", frames_used)
    print("profile_dim:", prof.shape[0])
    print("written:", OUTP.resolve())
    print("seconds:", round(time.time()-t0, 1))

if __name__ == "__main__":
    main()
