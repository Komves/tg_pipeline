import json, os, time
from pathlib import Path
from datetime import datetime, timedelta

RAW = Path("data/tg/raw/MIX")
OUT = Path("out/reports/a_rank_video_24h.jsonl")
MAX_HOURS = float(os.environ.get("A_VIDEO_MAX_HOURS", "24"))
MAX_VIDEOS = int(os.environ.get("A_VIDEO_MAX_VIDEOS", "300"))

def is_video(p: Path) -> bool:
    return p.name.startswith("msg_") and "_video." in p.name.lower()

def list_recent_videos():
    cutoff = datetime.now() - timedelta(hours=MAX_HOURS)
    vids = []
    for f in RAW.rglob("msg_*_video.*"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff:
                vids.append(f)
        except:
            pass
    vids.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return vids[:MAX_VIDEOS]

def main():
    vids = list_recent_videos()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # MVP: пока нет “вкуса”, пишем ранк = свежесть (mtime) как score.
    # Это НЕ финальный “taste”, но даёт устойчивый 24h-пул и быстрый цикл.
    # Следующим шагом подключим твой taste-профиль (как в B) и заменим score.
    with OUT.open("w", encoding="utf-8") as w:
        now = time.time()
        for f in vids:
            score = (f.stat().st_mtime - (now - MAX_HOURS*3600)) / (MAX_HOURS*3600)
            w.write(json.dumps({"path": str(f).replace("/", "\\"), "score": float(score), "mtime": f.stat().st_mtime}, ensure_ascii=False) + "\n")

    print(f"[A_VIDEO_RANK] wrote={len(vids)} -> {OUT}")

if __name__ == "__main__":
    main()
