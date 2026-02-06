from pathlib import Path
import re

p = Path(r".\tg_rank_b_clip_mix_fast_live.py")
s = p.read_text(encoding="utf-8")

# 1) Уменьшаем таймаут
s = re.sub(r"FFMPEG_TIMEOUT\s*=\s*\d+", "FFMPEG_TIMEOUT = 3", s)

# 2) Добавляем -threads 1 (часто снижает подвисания)
s = s.replace(
    '["ffmpeg","-hide_banner","-loglevel","error",\n         "-an","-sn",',
    '["ffmpeg","-hide_banner","-loglevel","error",\n         "-threads","1",\n         "-an","-sn",'
)

# 3) Логируем текущий файл
marker = "for vp in todo:"
insert = """for vp in todo:
            try:
                Path(r".\\\\out\\\\logs").mkdir(parents=True, exist_ok=True)
                Path(r".\\\\out\\\\logs\\\\rank_fast_current.txt").write_text(str(vp), encoding="utf-8")
            except:
                pass
"""
s = s.replace(marker, insert)

p.write_text(s, encoding="utf-8")
print("patched:", p)
