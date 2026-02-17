import os, json, time, subprocess, tempfile, shutil, html
from pathlib import Path

import numpy as np
from PIL import Image
import open_clip
import torch

SRC_JSONL = Path(r".\out\reports\b_rank_mix_fast_live.jsonl")
PROF_OK = Path(r".\out\b_profile_ok.npy")
PROF_NO = Path(r".\out\b_profile_no.npy")

OUT_JSONL = Path(r".\out\reports\b_rank_smart.jsonl")
OUT_HTML  = Path(r".\out\reports\b_top_smart.html")

OLD_MIN = 0.80
LAMBDA_NO = 0.15
TOP_N = 200

FFMPEG_TIMEOUT = 3
SEEK_T = 1.0

def extract_frame(video_path: str, t: float, out_jpg: str) -> bool:
    try:
        p = subprocess.run(
            ["ffmpeg","-hide_banner","-loglevel","error",
             "-threads","1","-an","-sn",
             "-ss", str(max(0.0,t)),
             "-i", video_path,
             "-frames:v","1",
             "-q:v","2",
             out_jpg],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=FFMPEG_TIMEOUT
        )
        return p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0
    except subprocess.TimeoutExpired:
        return False

def main():
    ok = np.load(str(PROF_OK)).astype(np.float32)
    ok = ok / (np.linalg.norm(ok) + 1e-12)

    has_no = PROF_NO.exists()
    if has_no:
        no = np.load(str(PROF_NO)).astype(np.float32)
        no = no / (np.linalg.norm(no) + 1e-12)
    else:
        no = None

    cands = []
    with open(SRC_JSONL, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                r = json.loads(line)
                sc = float(r.get("score", 0.0))
                if sc >= OLD_MIN:
                    cands.append(r["path"])
            except:
                pass

    cands = [p for p in dict.fromkeys(cands) if Path(p).exists()]

    print("[smart]")
    print("old_min:", OLD_MIN)
    print("candidates:", len(cands))
    print("lambda_no:", LAMBDA_NO, "has_no:", has_no)

    device="cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
    model.eval()

    tmpdir = Path(tempfile.mkdtemp(prefix="bsmart_"))
    out_rows = []
    fail = 0

    t0 = time.time()
    try:
        for i, vp in enumerate(cands, 1):
            jpg = tmpdir / f"f_{i}.jpg"
            if not extract_frame(vp, SEEK_T, str(jpg)):
                if not extract_frame(vp, 0.0, str(jpg)):
                    fail += 1
                    continue
            try:
                img = Image.open(jpg).convert("RGB")
                ten = preprocess(img).unsqueeze(0)
                with torch.no_grad():
                    feat = model.encode_image(ten)
                    feat = feat / feat.norm(dim=-1, keepdim=True)
                v = feat.squeeze(0).cpu().numpy().astype(np.float32)
                s_ok = float(v @ ok)
                s_no = float(v @ no) if has_no else 0.0
                s_new = s_ok - (LAMBDA_NO * s_no)
                out_rows.append({"path": vp, "score_ok": s_ok, "score_no": s_no, "score": s_new})
            except:
                fail += 1

            if i % 200 == 0:
                print(f"[progress] {i}/{len(cands)} done | fail={fail}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out_rows.sort(key=lambda r: r["score"], reverse=True)

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    top = out_rows[:TOP_N]
    cards = []
    for r in top:
        url = "file:///" + r["path"].replace("\\", "/")
        cards.append(f"""
        <div class="card">
          <div class="score">smart: {r['score']:.4f} | ok: {r['score_ok']:.4f} | no: {r['score_no']:.4f}</div>
          <a href="{html.escape(url)}">▶ Открыть</a>
          <div class="path">{html.escape(r['path'])}</div>
        </div>
        """)

    OUT_HTML.write_text(f"""<!DOCTYPE html><html lang="ru"><meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; background:#111; color:#eee; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 10px; }}
.card {{ background:#1c1c1c; padding:10px; border-radius:10px; }}
a {{ color:#4ea3ff; text-decoration:none; font-size:13px; }}
.score {{ font-size:12px; color:#aaa; margin-bottom:6px; }}
.path {{ font-size:11px; color:#888; margin-top:6px; word-break: break-all; }}
</style>
<body>
<h2>B SMART TOP (old_min={OLD_MIN})</h2>
<div>cands: {len(cands)} | scored: {len(out_rows)} | fail: {fail}</div>
<div class="grid">{''.join(cards)}</div>
</body></html>""", encoding="utf-8")

    print("\n[done]")
    print("cands:", len(cands))
    print("scored:", len(out_rows))
    print("fail:", fail)
    print("seconds:", round(time.time()-t0, 1))
    print("jsonl:", OUT_JSONL.resolve())
    print("html :", OUT_HTML.resolve())

    print("\nTOP10:")
    for r in out_rows[:10]:
        print("{:.4f} (ok {:.4f} no {:.4f})  {}".format(r["score"], r["score_ok"], r["score_no"], r["path"]))

if __name__ == "__main__":
    main()
