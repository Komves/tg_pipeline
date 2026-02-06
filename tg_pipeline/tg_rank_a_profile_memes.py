import os, json, time, subprocess, tempfile, shutil
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip, torch

ROOT = Path(__file__).resolve().parent

# A: берем только мем-папки (mp4) и ранжим по a_profile.npy
IN_DIR = ROOT / r"data\tg\raw\MIX"
PROFILE = ROOT / r"out\a_profile.npy"

OUT_JSONL = ROOT / r"out\reports\a_rank_memes.jsonl"
OUT_HTML  = ROOT / r"out\reports\a_top_memes.html"

THRESH = 0.0       # для A лучше не резать на старте, просто топ
TOPN = 60
MIN_SIZE = 1 * 1024 * 1024
FFMPEG_TIMEOUT = 12
SEEK_TIMES = [0.0, 0.5, 1.5, 2.5]

# whitelist мем-папок (можешь расширять)
ALLOW_FOLDERS = {
    "IT_Humor_VIP",
    "memomagia",
    "rjaka_memi",
    "smeh_proletariya",
    "smesnonet",
    "i_taaak_soidet",
    "delusion_generator",
    "dvestroki",
    "Hoduttut",
    "gusgagarik",
    "NoviyDubll",
}

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
    except Exception:
        return False

def build_html(rows):
    # rows: list of (score, path)
    items = rows[:TOPN]
    lines = []
    lines.append("<!doctype html><meta charset='utf-8'>")
    lines.append("<title>A Top (memes videos)</title>")
    lines.append("<style>body{font-family:Arial;margin:16px} .grid{max-width:1100px;margin:0 auto} .card{margin:14px 0;padding:12px;border:1px solid #ddd;border-radius:12px} video{width:100%;max-height:70vh;background:#000;border-radius:10px} .p{font-family:Consolas;font-size:12px;color:#555;word-break:break-all}</style>")
    lines.append("<div class='grid'>")
    lines.append(f"<h2>A Top (прикольные видео) — {len(items)} items</h2>")
    for i,(s,p) in enumerate(items,1):
        rel = Path(p).as_posix()
        lines.append("<div class='card'>")
        lines.append(f"<div><b>#{i}</b> score={s:.4f}</div>")
        lines.append(f"<video controls preload='metadata'><source src='{rel}' type='video/mp4'></video>")
        lines.append(f"<div class='p'>{p}</div>")
        lines.append("</div>")
    lines.append("</div>")
    return "\n".join(lines)

def main():
    if not PROFILE.exists():
        raise SystemExit("missing profile: " + str(PROFILE))

    prof = np.load(str(PROFILE)).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)

    # collect vids from allowed folders only
    vids=[]
    for d in ALLOW_FOLDERS:
        folder = IN_DIR / d
        if folder.exists():
            for p in folder.rglob("*.mp4"):
                try:
                    if p.stat().st_size >= MIN_SIZE:
                        vids.append(p)
                except:
                    pass

    print("[rank-a] videos:", len(vids))
    device="cpu"
    model,_,preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="ranka_"))
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    rows=[]
    fail=0
    t0=time.time()

    try:
        with open(OUT_JSONL, "w", encoding="utf-8") as out:
            for i,vp in enumerate(vids,1):
                best=None
                for t in SEEK_TIMES:
                    jpg = tmpdir / f"f_{i}_{int(t*10)}.jpg"
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
                        if (best is None) or (score > best):
                            best = score
                    except:
                        pass

                if best is None:
                    out.write(json.dumps({"path": str(vp), "status":"fail_frame"}, ensure_ascii=False) + "\n")
                    fail += 1
                else:
                    out.write(json.dumps({"path": str(vp), "status":"ok", "score": best}, ensure_ascii=False) + "\n")
                    rows.append((best, str(vp)))

                if i % 300 == 0:
                    print(f"[progress] {i}/{len(vids)} ok={len(rows)} fail={fail}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    rows.sort(reverse=True, key=lambda x: x[0])
    OUT_HTML.write_text(build_html(rows), encoding="utf-8")

    print("\n[done]")
    print("ok:", len(rows), "fail:", fail)
    print("seconds:", round(time.time()-t0,1))
    print("jsonl:", OUT_JSONL.resolve())
    print("html :", OUT_HTML.resolve())
    print("top10:", [round(x[0],4) for x in rows[:10]])

if __name__ == "__main__":
    main()
