from pathlib import Path
import re

p = Path(r".\watchdog_update_B_new.ps1")
s = p.read_text(encoding="utf-8")

# Добавим listPath и check new_files
if '$listPath' not in s:
    s = s.replace(
        '$hb    = Join-Path $root "out\\logs\\rank_fast_current.txt"\n',
        '$hb    = Join-Path $root "out\\logs\\rank_fast_current.txt"\n$listPath = Join-Path $root "out\\index\\mix_new_mp4.txt"\n'
    )

# Внутри цикла before stall-check: вычислим newCount
if 'newCount' not in s:
    s = s.replace(
        '$running = (Find-Worker | Measure-Object).Count\n\n  Log',
        '$running = (Find-Worker | Measure-Object).Count\n  $newCount = 0\n  try { if (Test-Path $listPath) { $newCount = (Get-Content -Encoding UTF8 $listPath | Where-Object { $_.Trim().Length -gt 0 } | Measure-Object).Count } } catch {}\n\n  Log'
    )

# Изменим условие stall: только если newCount > 0
s = s.replace(
    'if ($running -gt 0 -and $mj -ge $StallMinutes -and $mh -ge $StallMinutes) {',
    'if ($running -gt 0 -and $newCount -gt 0 -and $mj -ge $StallMinutes -and $mh -ge $StallMinutes) {'
)

# Добавим newCount в лог status (чтобы видеть)
s = s.replace(
    'Log ("[status] running={0} jsonl_stale_min={1} hb_stale_min={2}" -f $running, $mj, $mh)',
    'Log ("[status] running={0} new_files={1} jsonl_stale_min={2} hb_stale_min={3}" -f $running, $newCount, $mj, $mh)'
)

p.write_text(s, encoding="utf-8")
print("patched:", p)
