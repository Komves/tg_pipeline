import os, json, time, subprocess, tempfile, shutil
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

LIST_TXT = Path(r".\out\index\mix_new_mp4.txt")
PROFILE  = Path(r".\out\b_profile.npy")  # используем текущий профиль B
OUT_JSONL = Path(r".\out\reports\b_rank_mix_fast_live.jsonl")  # дописываем сюда
PROG_PATH = Path(r".\\out\\logs\\rank_new_progress.txt")
HB_PATH   = Path(r".\out\logs\rank_fast_current.txt")          # heartbeat
MIN_SIZE  = 1 * 1024 * 1024

FFMPEG_TIMEOUT = 3
SEEK_T = 1.0

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

def already_scored_set(jsonl_path: Path) -> set[str]:
    s = set()
    if not jsonl_path.exists():
        return s
    with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                r = json.loads(line)
                p = r.get("path")
                if p:
                    s.add(str(p))
            except:
                pass
    return s

def main():
    if not LIST_TXT.exists():
        print("[rank-new] list not found:", LIST_TXT.resolve())
        return

    if not PROFILE.exists():
        raise SystemExit("missing profile: " + str(PROFILE.resolve()))

    prof = np.load(str(PROFILE)).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)

    raw = [ln.strip() for ln in LIST_TXT.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    # оставим только существующие mp4 >= 1MB
    paths = []
    for p in raw:
        try:
            if os.path.exists(p) and os.path.getsize(p) >= MIN_SIZE:
                paths.append(p)
        except:
            pass

    scored = already_scored_set(OUT_JSONL)
    todo = [p for p in paths if p not in scored]

    print("[rank-new] candidates_in_list:", len(paths))
    print("[rank-new] already_scored:", len(paths) - len(todo))
    print("[rank-new] todo:", len(todo))
    PROG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROG_PATH.write_text(f"done=0 total={len(todo)}
", encoding="utf-8")

    if not todo:
        print("[rank-new] nothing to do")
        return

    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="ranknew_"))
    ok = fail = 0
    t0 = time.time()

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    HB_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(OUT_JSONL, "a", encoding="utf-8") as out:
            for i, vp in enumerate(todo, 1):
                try:
                    HB_PATH.write_text(vp, encoding="utf-8")
                except:
                    pass

                jpg = tmpdir / f"f_{i}.jpg"
                if not extract_frame(vp, SEEK_T, str(jpg)):
                    if not extract_frame(vp, 0.0, str(jpg)):
                        out.write(json.dumps({"path": vp, "status":"fail_frame"}, ensure_ascii=False) + "\n"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=fail_frame\n", encoding="utf-8"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=fail_frame\n", encoding="utf-8")
                        fail += 1
                        continue

                try:
                    img = Image.open(jpg).convert("RGB")
                    ten = preprocess(img).unsqueeze(0)
                    with torch.no_grad():
                        feat = model.encode_image(ten)
                        feat = feat / feat.norm(dim=-1, keepdim=True)
                    v = feat.squeeze(0).cpu().numpy().astype(np.float32)
                    score = float(v @ prof)
                    out.write(json.dumps({"path": vp, "status":"ok", "score": score}, ensure_ascii=False) + "\n"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=ok\n", encoding="utf-8"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=ok\n", encoding="utf-8")
                    ok += 1
                except:
                    out.write(json.dumps({"path": vp, "status":"fail_embed"}, ensure_ascii=False) + "\n"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=fail_embed\n", encoding="utf-8"); out.flush()
                    PROG_PATH.write_text(f"done={i} total={len(todo)}\ncurrent={vp}\nstatus=fail_embed\n", encoding="utf-8")
                    fail += 1

                if i % 5 == 0:
                    print(f"[progress] {i}/{len(todo)} ok={ok} fail={fail}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n[done]")
    print("todo:", len(todo))
    print("ok:", ok, "fail:", fail)
    print("seconds:", round(time.time()-t0, 1))
    print("jsonl:", OUT_JSONL.resolve())

if __name__ == "__main__":
    main()
