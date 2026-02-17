import os, json, time, subprocess, tempfile, shutil, traceback
from pathlib import Path
import numpy as np
from PIL import Image
import open_clip
import torch
ROOT = Path(r"C:\\Users\\Марк\\tg_pipeline\\tg_pipeline")
IN_DIR = Path(r"C:\\Users\\Марк\\tg_pipeline\\tg_pipeline\\out\\tmp\\mix_new_run")
PROFILE = ROOT / r"out\b_profile_ok.npy"

OUT_JSONL = Path(r"C:\\Users\\Марк\\tg_pipeline\\tg_pipeline\\out\\reports\\b_rank_daily.jsonl.tmp")
OUT_HTML  = Path(r"C:\\Users\\Марк\\tg_pipeline\\tg_pipeline\\out\\reports\\b_top_daily.html")

THRESH = 0.84
MIN_SIZE = 1 * 1024 * 1024
FFMPEG_TIMEOUT = 3
SEEK_TIMES = [0.5, 1.5, 2.5]

# new: observability / safety
PROGRESS_EVERY = 50          # print progress every N videos
SLOW_VIDEO_SEC = 15          # warn if a single video takes too long
FLUSH_EVERY = 1              # flush jsonl every N rows (1 = safest)

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

def main():
    if not PROFILE.exists():
        raise SystemExit("missing profile: " + str(PROFILE))

    prof = np.load(str(PROFILE)).astype(np.float32)
    prof = prof / (np.linalg.norm(prof) + 1e-12)

    vids = []
    for p in IN_DIR.rglob("*.mp4"):
        try:
            if p.stat().st_size >= MIN_SIZE:
                vids.append(p)
        except Exception:
            pass

    print("[rank-ok] videos:", len(vids))
    print("[rank-ok] thresh:", THRESH)

    # make CPU behavior more predictable
    try:
        torch.set_num_threads(1)
    except Exception:
        pass

    device = "cpu"
    # NOTE: first create_model_and_transforms can take time; we log it.
    t_model0 = time.time()
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()
    print("[rank-ok] model_ready_sec:", round(time.time() - t_model0, 2))

    tmpdir = Path(tempfile.mkdtemp(prefix="rankok_"))
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    fail = 0
    t0 = time.time()

    broken_tsv = OUT_JSONL.with_suffix(".broken_mp4.tsv")
    # line-buffered JSONL: buffering=1 (text mode) + explicit flush
    try:
        with open(OUT_JSONL, "w", encoding="utf-8", buffering=1) as out, \
             open(broken_tsv, "a", encoding="utf-8") as brok:

            # header-ish marker (helps you see file non-empty early)
            out.write(json.dumps({"ts": time.time(), "status": "start", "videos": len(vids)}, ensure_ascii=False) + "\n")
            out.flush()

            for i, vp in enumerate(vids, 1):
                t_vid0 = time.time()
                best = None
                status = "ok"
                err = None

                try:
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
                        except Exception:
                            # keep trying other frames
                            pass

                    if best is None:
                        status = "fail_frame"
                        fail += 1
                        out.write(json.dumps({"path": str(vp), "status": status}, ensure_ascii=False) + "\n")
                    else:
                        out.write(json.dumps({"path": str(vp), "status": "ok", "score": best}, ensure_ascii=False) + "\n")
                        if best >= THRESH:
                            rows.append((best, str(vp)))

                except Exception as e:
                    status = "error"
                    fail += 1
                    err = repr(e)
                    out.write(json.dumps({"path": str(vp), "status": status, "error": err}, ensure_ascii=False) + "\n")
                    brok.write(f"{time.time()}\t{status}\t{vp}\t{err}\n")

                # flush to disk so tmp size grows and Ctrl+C doesn't lose everything
                if (i % FLUSH_EVERY) == 0:
                    out.flush()

                dt = time.time() - t_vid0
                if dt >= SLOW_VIDEO_SEC:
                    print(f"[slow] {i}/{len(vids)} sec={dt:.1f} status={status} file={vp.name}")

                if (i % PROGRESS_EVERY) == 0 or i == 1:
                    elapsed = time.time() - t0
                    rate = i / max(elapsed, 1e-6)
                    print(f"[progress] {i}/{len(vids)} kept={len(rows)} fail={fail} rate={rate:.2f}/s last={vp.name}")

            # end marker
            out.write(json.dumps({"ts": time.time(), "status": "done", "kept": len(rows), "fail": fail,
                                 "seconds": round(time.time()-t0, 1)}, ensure_ascii=False) + "\n")
            out.flush()

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    rows.sort(reverse=True, key=lambda x: x[0])

    print("\n[done]")
    print("kept>=thr:", len(rows))
    print("fail:", fail)
    print("seconds:", round(time.time()-t0,1))
    print("jsonl:", OUT_JSONL.resolve())
    print("broken:", broken_tsv.resolve())
    print("top10:", [round(x[0],4) for x in rows[:10]])

if __name__ == "__main__":
    main()

