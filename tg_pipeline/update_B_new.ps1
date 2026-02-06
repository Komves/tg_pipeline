param(
  [double]$Thresh = 0.84
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
$mixin = Join-Path $root "data\tg\raw\MIX"

$stateDir = Join-Path $root "out\logs"
$listDir  = Join-Path $root "out\index"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
New-Item -ItemType Directory -Force -Path $listDir  | Out-Null

$lastRunPath = Join-Path $stateDir "b_rank_last_run.txt"
$listPath    = Join-Path $listDir  "mix_new_mp4.txt"

# читаем last_run, иначе берём "вчера"
if (Test-Path $lastRunPath) {
  $last = Get-Content -Encoding UTF8 $lastRunPath | Select-Object -First 1
  $since = [datetime]::Parse($last)
} else {
  $since = (Get-Date).AddDays(-1)
}

Write-Host "[update_B_new] since: $since"
Write-Host "[update_B_new] mix:   $mixin"
Write-Host "[update_B_new] thresh: $Thresh"

# собираем новые/изменённые mp4 >=1MB по LastWriteTime
$files = Get-ChildItem -Path $mixin -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -match '^\.(mp4|mkv|mov)$' -and $_.Length -ge 1MB -and $_.LastWriteTime -gt $since } |
  Sort-Object LastWriteTime |
  Select-Object -ExpandProperty FullName

if ($files.Count -gt 0) { $files | Set-Content -Encoding UTF8 $listPath } else { "" | Set-Content -Encoding UTF8 $listPath }
Write-Host "[update_B_new] new_files:" $files.Count
Write-Host "[update_B_new] list:" $listPath

# ранжируем только новые и дописываем в общий jsonl
if ($files.Count -gt 0) { & $py .\tg_rank_b_clip_list_fast.py } else { Write-Host "[update_B_new] skip rank (no new files)" }

# дальше всё как раньше: пересобрать TOP по порогу и обновить очередь
& $py -c "import pathlib,re; p=pathlib.Path('make_b_top_from_jsonl.py'); s=p.read_text(encoding='utf-8'); s=re.sub(r'THRESH\s*=\s*0\.\d+','THRESH = %.2f'%($Thresh),s); p.write_text(s,encoding='utf-8')"
& $py .\make_b_top_from_jsonl.py
& $py .\export_b_candidates.py
& $py .\dedup_mvp.py --root .\data\tg\raw\B_candidates --db .\out\index\dedup_b_candidates.sqlite
& $py .\make_b_candidates_canonical.py
& $py .\make_b_queue_html.py

# фиксируем last_run как текущий момент
(Get-Date).ToString("o") | Set-Content -Encoding UTF8 $lastRunPath
Write-Host "[update_B_new] last_run saved:" $lastRunPath
Write-Host "[update_B_new] DONE"
