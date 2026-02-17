from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List


# NOTE:
# - This module writes "clip_emb" into "<file>.meta.json"
# - It expects ffmpeg + ffprobe available on Render
# - It tries to use open_clip + torch if installed, otherwise returns False gracefully

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


@dataclass
class EmbedResult:
    emb: list[float]
    frames_used: int


def _ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _extract_frames_ffmpeg(video: Path, out_dir: Path, n: int = 12) -> List[Path]:
    """
    Extract ~n frames uniformly from the whole video into out_dir as JPG.
    We do a simple 'fps' sampling; it's not perfect but stable and fast.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use fps sampling with a cap on output count using -frames:v
    # We set fps low and also cap by -frames:v n to avoid huge output.
    out_pattern = str(out_dir / "f_%04d.jpg")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", "fps=0.5",        # sample 0.5 frame/sec, then cap by frames:v
        "-frames:v", str(n),
        "-q:v", "3",
        out_pattern,
    ]
    subprocess.run(cmd, check=False)

    frames = sorted(out_dir.glob("f_*.jpg"))
    if len(frames) > n:
        frames = frames[:n]
    return frames


def _load_meta(meta_path: Path) -> dict:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(meta_path: Path, j: dict) -> bool:
    try:
        meta_path.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def _clip_image_emb(frames: List[Path]) -> Optional[EmbedResult]:
    """
    Compute one embedding as mean over frame embeddings.
    Returns None if open_clip/torch are not available.
    """
    try:
        import torch  # type: ignore
        import open_clip  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return None

    device = "cpu"

    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model.eval()
    model.to(device)

    embs = []
    with torch.no_grad():
        for fp in frames:
            try:
                img = Image.open(fp).convert("RGB")
                x = preprocess(img).unsqueeze(0).to(device)
                e = model.encode_image(x)
                e = e / e.norm(dim=-1, keepdim=True)
                embs.append(e.cpu())
            except Exception:
                continue

    if not embs:
        return None

    mean = torch.mean(torch.cat(embs, dim=0), dim=0)
    mean = mean / mean.norm()

    return EmbedResult(emb=mean.numpy().astype("float32").tolist(), frames_used=len(embs))


def ensure_meta_clip_emb(video_path: str) -> bool:
    """
    Ensure <video>.meta.json exists and has clip_emb.
    Returns True if clip_emb present/written, False otherwise.
    """
    p = Path(video_path)
    if not p.exists() or not p.is_file():
        return False
    if p.suffix.lower() not in VIDEO_EXT:
        return False

    meta_path = Path(str(p) + ".meta.json")
    if not meta_path.exists():
        return False

    j = _load_meta(meta_path)
    if isinstance(j.get("clip_emb"), list) and len(j["clip_emb"]) > 0:
        return True

    if not _ffmpeg_exists():
        # can't do anything
        return False

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        frames = _extract_frames_ffmpeg(p, out_dir, n=12)
        if not frames:
            return False

        res = _clip_image_emb(frames)
        if res is None:
            # deps missing
            return False

        j["clip_emb"] = res.emb
        j["clip_frames_used"] = res.frames_used

        return _save_meta(meta_path, j)
