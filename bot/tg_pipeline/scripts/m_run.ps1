param(
  [int]$N = 70,
  [int]$HOURS = 24
)

$ErrorActionPreference = "Stop"

function Run-Ingest {
  Write-Host "== INGEST =="

  # 1) если есть явная настройка (опционально)
  $cfg = "out\config\m_ingest_cmd.txt"
  if(Test-Path $cfg){
    $cmd = (Get-Content $cfg -Encoding UTF8 | Select-Object -First 1).Trim()
    if($cmd){
      Write-Host "INGEST CMD (from $cfg): $cmd"
      Invoke-Expression $cmd
      return $true
    }
  }

  # 2) авто-поиск ingest-скриптов
  $candidates = @()
  if(Test-Path "scripts"){
    $candidates += Get-ChildItem scripts -File -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match "ingest" -and ($_.Extension -in @(".ps1",".py"))
      } |
      Sort-Object LastWriteTime -Descending
  }

  if($candidates.Count -eq 0){
    Write-Host "WARNING: ingest not found. Put your ingest cmd into out\config\m_ingest_cmd.txt (one line)."
    return $false
  }

  # если несколько — берём самый свежий и печатаем список
  if($candidates.Count -gt 1){
    Write-Host "Found multiple ingest candidates. Using most recent:"
    $candidates | Select-Object -First 10 Name,LastWriteTime | Format-Table -AutoSize
  } else {
    Write-Host "Found ingest:"
    $candidates | Select-Object Name,LastWriteTime | Format-Table -AutoSize
  }

  $pick = $candidates[0].FullName
  Write-Host "RUN: $pick"

  if($pick.ToLower().EndsWith(".ps1")){
    powershell -NoProfile -ExecutionPolicy Bypass -File $pick
  } else {
    py $pick
  }

  return $true
}

# --- 0) before/after sanity: покажем сколько "свежих" файлов в MIX за последние HOURS ---
function Count-Fresh {
  param([int]$H)
  $mix = "data\tg\raw\MIX"
  if(-not (Test-Path $mix)){ return 0 }
  $cut = (Get-Date).AddHours(-$H)
  (Get-ChildItem $mix -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -ge $cut }).Count
}

$before = Count-Fresh -H $HOURS
Write-Host "Fresh files in MIX (last $HOURS h) BEFORE ingest: $before"

$ran = Run-Ingest

$after = Count-Fresh -H $HOURS
Write-Host "Fresh files in MIX (last $HOURS h) AFTER ingest:  $after"

# --- 1) REVIEW SERVER ---
Write-Host "== REVIEW SERVER =="
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-Command","py scripts\m_review_server.py"
Start-Sleep -Milliseconds 500

# --- 2) SMART BATCH (24h) ---
Write-Host "== SMART BATCH =="

$env:N = "$N"
$env:MAX_AGE_HOURS = "$HOURS"
$env:EXPLORE_FRAC = "0.20"
$env:TASTE_WEIGHT = "0.50"
$env:SUPER_BOOST = "3.0"

py scripts\m_next_smart.py
