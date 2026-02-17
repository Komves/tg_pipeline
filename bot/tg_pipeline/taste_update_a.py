import json
import os
from pathlib import Path
import numpy as np
from PIL import Image
import open_clip
import torch
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT/"out"/"config"/"engine.local.json").read_text(encoding="utf-8-sig"))
LOGS = Path(CFG["logs_dir"])
FB = LOGS/"a_feedback.tsv"
OUT = ROOT/"out"/"a_profile.npy"

SEEK_TIMES = [0.5, 1.5, 2.5]
FFMPEG_TIMEOUT = 3

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
    if not FB.exists():
        raise SystemExit(f"missing feedback: {FB}")

    ok_paths = []
    lines = FB.read_text(encoding="utf-8").splitlines()
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) < 4: 
            continue
        _, label, _, path = parts[0], parts[1], parts[2], parts[3]
        if label.strip().upper() == "OK":
            ok_paths.append(path)

    ok_paths = [p for p in ok_paths if Path(p).exists()]
    print("[A] OK videos:", len(ok_paths))
    if len(ok_paths) < 5:
        raise SystemExit("need at least 5 OK to build a_profile.npy")

    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    feats = []
    tmpdir = Path(tempfile.mkdtemp(prefix="tasteA_"))

    try:
        for i, vp in enumerate(ok_paths, 1):
            best = None
            for t in SEEK_TIMES:
                jpg = tmpdir / f"f_{i}_{int(t*10)}.jpg"
                if not extract_frame(vp, t, str(jpg)):
                    continue
                try:
                    img = Image.open(jpg).convert("RGB")
                    ten = preprocess(img).unsqueeze(0)
                    with torch.no_grad():
                        feat = model.encode_image(ten)
                        feat = feat / feat.norm(dim=-1, keepdim=True)
                    v = feat.squeeze(0).cpu().numpy().astype(np.float32)
                    best = v if best is None else best
                    # choose first successful frame (fast + stable)
                    break
                except Exception:
                    continue
            if best is not None:
                feats.append(best)

    finally:
        try:
            for p in tmpdir.glob("*"):
                p.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass

    if len(feats) < 5:
        raise SystemExit("not enough frames extracted from OK videos")

    prof = np.stack(feats, axis=0).mean(axis=0).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)
    np.save(str(OUT), prof)
    print("[A] saved:", OUT)

if __name__ == "__main__":
    main()
