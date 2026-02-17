from pathlib import Path
import re

p = Path(r".\update_B_new.ps1")
s = p.read_text(encoding="utf-8")

# 1) гарантируем создание файла списка даже при 0 файлов
s = s.replace(
  '$files | Set-Content -Encoding UTF8 $listPath',
  'if ($files.Count -gt 0) { $files | Set-Content -Encoding UTF8 $listPath } else { "" | Set-Content -Encoding UTF8 $listPath }'
)

# 2) если новых файлов нет — не запускаем ранкер
#    (оставляем сборку TOP/очереди как раньше)
s = s.replace(
  '& $py .\\tg_rank_b_clip_list_fast.py',
  'if ($files.Count -gt 0) { & $py .\\tg_rank_b_clip_list_fast.py } else { Write-Host "[update_B_new] skip rank (no new files)" }'
)

p.write_text(s, encoding="utf-8")
print("patched:", p)
