import json, subprocess, tempfile
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip, torch

ROOT = Path(__file__).resolve().parent
CFG  = json.loads((ROOT/"out/config/engine.local.json").read_text(encoding="utf-8-sig"))

FEEDBACK = ROOT/"out/logs/a_feedback_1.tsv"
OUT_PROF = ROOT/"out/a_profile.npy"

SEEK_TIMES = [0.0, 0.5, 1.5, 2.5]
FFMPEG_TIMEOUT = 20

if not FEEDBACK.exists():
    raise SystemExit(f"missing: {FEEDBACK}")

def read_ok(tsv: Path):
    lines = tsv.read_text(encoding="utf-8", errors="ignore").splitlines()
    out=[]
    for l in lines[1:]:
        parts = l.split("\t")
        if len(parts) >= 4 and parts[1].strip().upper() == "OK":
            out.append(parts[3].strip())
    # unique keep order
    seen=set()
    uniq=[]
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq

ok_paths = read_ok(FEEDBACK)
if len(ok_paths) < 5:
    raise SystemExit(f"need >=5 OK paths, got {len(ok_paths)}")

device="cpu"
model,_,preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
model.eval()

tmpdir = Path(tempfile.mkdtemp(prefix="aprofile_"))

def extract_frame(video_path: str, tag: str):
    # unique output file per (video, seek)
    for t in SEEK_TIMES:
        out = tmpdir / f"f_{tag}_{int(t*10)}.jpg"
        try:
            p = subprocess.run(
                ["ffmpeg","-hide_banner","-loglevel","error",
                 "-threads","1","-an","-sn",
                 "-ss", str(max(0.0,t)),
                 "-i", video_path,
                 "-frames:v","1","-q:v","2", str(out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=FFMPEG_TIMEOUT
            )
            if p.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return out
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
    return None

feats=[]
bad=0
for idx, p in enumerate(ok_paths, 1):
    src = Path(p)
    if not src.exists():
        bad += 1
        continue

    jpg = extract_frame(str(src), f"{idx:03d}")
    if not jpg:
        bad += 1
        continue

    try:
        img = Image.open(jpg).convert("RGB")
        ten = preprocess(img).unsqueeze(0)
        with torch.no_grad():
            f = model.encode_image(ten)
            f = f / f.norm(dim=-1, keepdim=True)
        feats.append(f.squeeze(0).cpu().numpy().astype("float32"))
    except Exception:
        bad += 1

if len(feats) < 5:
    raise SystemExit(f"too few usable samples: used={len(feats)} bad={bad} (paths={len(ok_paths)})")

prof = np.mean(np.stack(feats,0), axis=0)
prof = prof / (np.linalg.norm(prof)+1e-12)
np.save(OUT_PROF, prof)

print("OK. a_profile.npy:", OUT_PROF)
print("OK. ok_paths:", len(ok_paths), "used:", len(feats), "bad:", bad)
