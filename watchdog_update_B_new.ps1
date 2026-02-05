param(
  [int]$StallMinutes = 10,
  [int]$CheckSeconds = 30,
  [double]$Thresh = 0.84
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$logDir = Join-Path $root "out\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$wdLog = Join-Path $logDir "watchdog_update_B_new.log"

$jsonl = Join-Path $root "out\reports\b_rank_mix_fast_live.jsonl"
$hb    = Join-Path $root "out\logs\rank_fast_current.txt"
$listPath = Join-Path $root "out\index\mix_new_mp4.txt"

function Log($msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  $line | Tee-Object -FilePath $wdLog -Append
}

function LastWrite($path) {
  try { if (Test-Path $path) { return (Get-Item $path).LastWriteTime } } catch {}
  return $null
}

function MinutesStale($dt) {
  if ($null -eq $dt) { return 999999 }
  return (New-TimeSpan -Start $dt -End (Get-Date)).TotalMinutes
}

function Find-Worker() {
  Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "python\.exe|pwsh\.exe|powershell\.exe" -and
    (
      $_.CommandLine -match "tg_rank_b_clip_list_fast\.py" -or
      $_.CommandLine -match "update_B_new\.ps1"
    )
  }
}

function Kill-Worker() {
  Log "[action] kill ffmpeg + worker"
  try { Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
  foreach ($p in (Find-Worker)) {
    try {
      Log ("[kill] pid={0} name={1}" -f $p.ProcessId, $p.Name)
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    } catch {}
  }
}

function Start-Update() {
  Log ("[start] update_B_new.ps1 -Thresh {0}" -f $Thresh)
  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy","Bypass",
    "-File",(Join-Path $root "update_B_new.ps1"),
    "-Thresh",$Thresh
  ) | Out-Null
}

Log ("[watchdog] started stall={0}min check={1}s thresh={2}" -f $StallMinutes, $CheckSeconds, $Thresh)
Log ("[watchdog] jsonl={0}" -f $jsonl)
Log ("[watchdog] hb={0}" -f $hb)

if ((Find-Worker | Measure-Object).Count -eq 0) { Start-Update } else { Log "[watchdog] worker already running" }

while ($true) {
  $mj = [math]::Round((MinutesStale (LastWrite $jsonl)), 2)
  $mh = [math]::Round((MinutesStale (LastWrite $hb)), 2)
  $running = (Find-Worker | Measure-Object).Count
  $newCount = 0
  try { if (Test-Path $listPath) { $newCount = (Get-Content -Encoding UTF8 $listPath | Where-Object { $_.Trim().Length -gt 0 } | Measure-Object).Count } } catch {}

  Log ("[status] running={0} new_files={1} jsonl_stale_min={2} hb_stale_min={3}" -f $running, $newCount, $mj, $mh)

  if ($running -gt 0 -and $newCount -gt 0 -and $mj -ge $StallMinutes -and $mh -ge $StallMinutes) {
    Log ("[stall] >= {0}min detected. restarting..." -f $StallMinutes)
    Kill-Worker
    Start-Sleep -Seconds 3
    Start-Update
  }

  Start-Sleep -Seconds $CheckSeconds
}
