import os, subprocess, tempfile, shutil, math
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

SEEDS = Path(r".\out\index\b_seeds.txt")
OUT_PROFILE = Path(r".\out\b_profile.npy")

# параметры кадрирования
FRAMES_PER_VIDEO = 3  # начало/середина/конец
MAX_VIDEOS = 0        # 0 = все
IMG_SIZE = 224

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def ffprobe_duration_seconds(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    p = run(cmd)
    if p.returncode != 0:
        return float("nan")
    try:
        return float(p.stdout.strip())
    except:
        return float("nan")

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    # -ss before -i is faster
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(max(0.0, t)),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        out_jpg
    ]
    p = run(cmd)
    return p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0

def load_image_rgb(p: str) -> Image.Image:
    img = Image.open(p).convert("RGB")
    return img

def main():
    paths = [l.strip() for l in SEEDS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if MAX_VIDEOS and len(paths) > MAX_VIDEOS:
        paths = paths[:MAX_VIDEOS]

    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    all_feats = []
    used_videos = 0
    used_frames = 0
    skipped = 0

    tmpdir = Path(tempfile.mkdtemp(prefix="bprof_"))
    try:
        for vp in paths:
            if not os.path.exists(vp):
                skipped += 1
                continue

            dur = ffprobe_duration_seconds(vp)
            if not (dur and dur == dur) or dur <= 0.2:
                # fallback: try mid anyway
                ts = [0.0]
            else:
                # точки: ~10%, 50%, 90% (чтобы не ловить черные первые кадры)
                ts = [dur*0.10, dur*0.50, dur*0.90][:FRAMES_PER_VIDEO]

            frame_paths = []
            ok_any = False
            for i, t in enumerate(ts):
                out_jpg = str(tmpdir / f"frame_{used_videos:05d}_{i}.jpg")
                if extract_frame(vp, t, out_jpg):
                    frame_paths.append(out_jpg)
                    ok_any = True

            if not ok_any:
                skipped += 1
                continue

            # encode frames
            imgs = []
            for fp in frame_paths:
                try:
                    img = load_image_rgb(fp)
                    imgs.append(preprocess(img).unsqueeze(0))
                except:
                    pass

            if not imgs:
                skipped += 1
                continue

            batch = torch.cat(imgs, dim=0)
            with torch.no_grad():
                feats = model.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            feats_np = feats.cpu().numpy()
            for v in feats_np:
                all_feats.append(v)
            used_frames += feats_np.shape[0]
            used_videos += 1

            if used_videos % 10 == 0:
                print(f"[progress] videos_used={used_videos} frames_used={used_frames} skipped={skipped}")

        if not all_feats:
            raise SystemExit("No frames encoded. Check seeds/ffmpeg.")

        mat = np.stack(all_feats, axis=0)
        prof = mat.mean(axis=0)
        prof = prof / np.linalg.norm(prof)

        OUT_PROFILE.parent.mkdir(parents=True, exist_ok=True)
        np.save(str(OUT_PROFILE), prof.astype(np.float32))

        print("\n[done]")
        print("seed_paths:", len(paths))
        print("videos_used:", used_videos)
        print("frames_used:", used_frames)
        print("skipped:", skipped)
        print("profile_dim:", prof.shape[0])
        print("written:", str(OUT_PROFILE.resolve()))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()
