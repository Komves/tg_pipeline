param(
  [double]$Thresh = 0.84,
  [int]$StallMinutes = 10,
  [int]$CheckSeconds = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$wd = Join-Path $root "watchdog_update_B_new.ps1"
$upd = Join-Path $root "update_B_new.ps1"

if (!(Test-Path $wd)) { throw "missing: $wd" }
if (!(Test-Path $upd)) { throw "missing: $upd" }

# Проверим, не запущен ли уже watchdog_update_B_new
$already = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match "powershell\.exe|pwsh\.exe" -and $_.CommandLine -match "watchdog_update_B_new\.ps1"
} | Measure-Object | Select-Object -ExpandProperty Count

if ($already -eq 0) {
  Write-Host "[run_B] starting watchdog in a new window..."
  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy","Bypass",
    "-File",$wd,
    "-StallMinutes",$StallMinutes,
    "-CheckSeconds",$CheckSeconds,
    "-Thresh",$Thresh
  ) | Out-Null
} else {
  Write-Host "[run_B] watchdog already running"
}

Write-Host "[run_B] running one update now..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $upd -Thresh $Thresh

Write-Host ""
Write-Host "[run_B] DONE"
Write-Host "Queue HTML:  $root\out\reports\b_queue_candidates.html"
Write-Host "Review HTML: $root\out\reports\b_queue_review.html"
Write-Host "Logs:        $root\out\logs\watchdog_update_B_new.log"
Write-Host "Progress:    $root\out\logs\rank_new_progress.txt"
