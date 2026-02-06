from __future__ import annotations
from pathlib import Path
import subprocess, tempfile, shutil, time
import numpy as np
from PIL import Image
import open_clip
import torch

REVIEW_TSV = Path(r".\out\reports\b_ready_review.tsv")
OUT_OK = Path(r".\out\b_profile_ok.npy")
OUT_NO = Path(r".\out\b_profile_no.npy")

FRAMES_PER_VIDEO = 3
TIMES = [0.5, 1.5, 2.5]  # секунды
FFMPEG_TIMEOUT = 5

def extract_frame(video: str, t: float, out_jpg: str) -> bool:
    # -ss before -i is faster; -an/-sn to avoid audio/sub overhead
    try:
        p = subprocess.run(
            ["ffmpeg","-hide_banner","-loglevel","error",
             "-threads","1",
             "-an","-sn",
             "-ss", str(max(0.0, t)),
             "-i", video,
             "-frames:v","1",
             "-q:v","2",
             out_jpg],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=FFMPEG_TIMEOUT
        )
        return p.returncode == 0 and Path(out_jpg).exists() and Path(out_jpg).stat().st_size > 0
    except subprocess.TimeoutExpired:
        return False

def load_paths():
    ok, no = [], []
    with open(REVIEW_TSV, "r", encoding="utf-8", errors="ignore") as f:
        header = f.readline()
        for line in f:
            if not line.strip():
                continue
            parts = line.split("\t")
            while len(parts) < 4:
                parts.append("")
            _rank, status, path, _note = parts[:4]
            status = status.strip().lower()
            path = path.strip()
            if not path:
                continue
            if status == "ok":
                ok.append(path)
            elif status == "no":
                no.append(path)
    return ok, no

def embed_videos(video_paths, model, preprocess):
    tmpdir = Path(tempfile.mkdtemp(prefix="bprof_"))
    embs = []
    used_videos = 0
    fail_videos = 0
    used_frames = 0

    try:
        for vp in video_paths:
            frame_paths = []
            # пытаемся 3 таймкода, если не вышло — fallback на 0.0
            for t in TIMES[:FRAMES_PER_VIDEO]:
                out_j = tmpdir / f"f_{used_videos}_{int(t*1000)}.jpg"
                if extract_frame(vp, t, str(out_j)):
                    frame_paths.append(out_j)
            if not frame_paths:
                out_j = tmpdir / f"f_{used_videos}_0.jpg"
                if extract_frame(vp, 0.0, str(out_j)):
                    frame_paths.append(out_j)

            if not frame_paths:
                fail_videos += 1
                continue

            feats_list = []
            for fp in frame_paths:
                try:
                    img = Image.open(fp).convert("RGB")
                    ten = preprocess(img).unsqueeze(0)
                    with torch.no_grad():
                        feat = model.encode_image(ten)
                        feat = feat / feat.norm(dim=-1, keepdim=True)
                    feats_list.append(feat.squeeze(0).cpu().numpy().astype(np.float32))
                    used_frames += 1
                except:
                    pass

            if not feats_list:
                fail_videos += 1
                continue

            # видео-эмбеддинг = среднее по кадрам (можно заменить на max позже)
            v = np.mean(np.stack(feats_list, axis=0), axis=0)
            v = v / (np.linalg.norm(v) + 1e-12)
            embs.append(v)
            used_videos += 1

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return np.array(embs, dtype=np.float32), used_videos, fail_videos, used_frames

def save_profile(vecs: np.ndarray, out_path: Path):
    prof = vecs.mean(axis=0)
    prof = prof / (np.linalg.norm(prof) + 1e-12)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(out_path), prof)
    return prof

def main():
    if not REVIEW_TSV.exists():
        raise SystemExit(f"missing: {REVIEW_TSV}")

    ok_paths, no_paths = load_paths()
    print("[input]")
    print("ok:", len(ok_paths))
    print("no:", len(no_paths))

    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    t0 = time.time()

    if ok_paths:
        ok_vecs, ok_used, ok_fail, ok_frames = embed_videos(ok_paths, model, preprocess)
        if len(ok_vecs) == 0:
            raise SystemExit("no OK embeddings produced (all failed)")
        save_profile(ok_vecs, OUT_OK)
        print("\n[ok_profile]")
        print("videos_used:", ok_used, "videos_failed:", ok_fail, "frames_used:", ok_frames)
        print("written:", OUT_OK.resolve())
    else:
        print("\n[ok_profile] skipped (no ok labels)")

    if no_paths:
        no_vecs, no_used, no_fail, no_frames = embed_videos(no_paths, model, preprocess)
        if len(no_vecs) > 0:
            save_profile(no_vecs, OUT_NO)
            print("\n[no_profile]")
            print("videos_used:", no_used, "videos_failed:", no_fail, "frames_used:", no_frames)
            print("written:", OUT_NO.resolve())
        else:
            print("\n[no_profile] no embeddings produced (all failed)")
    else:
        print("\n[no_profile] skipped (no no labels)")

    print("\n[done]")
    print("seconds:", round(time.time() - t0, 1))

if __name__ == "__main__":
    main()
