param(
  [string]$DeliveryDir,
  [string]$Ok = ""  # e.g. "1,3,7"
)

$ErrorActionPreference = "Stop"
function Log([string]$msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host ("[$ts] " + $msg)
}

if (-not $DeliveryDir) { throw "Usage: -DeliveryDir <path> -Ok '1,3,7'" }

$cfgPath = Join-Path $PSScriptRoot "out\config\engine.local.json"
$cfg = Get-Content $cfgPath | ConvertFrom-Json

$manifest = Join-Path $DeliveryDir "manifest.tsv"
if (-not (Test-Path $manifest)) { throw "missing manifest: $manifest" }

$fb = Join-Path $cfg.logs_dir "b_feedback.tsv"
New-Item -ItemType Directory -Force -Path $cfg.logs_dir | Out-Null
if (-not (Test-Path $fb)) { "ts`tlabel`tscore`tpath" | Set-Content -Encoding UTF8 $fb }

# parse ok indices
$okSet = New-Object 'System.Collections.Generic.HashSet[int]'
if ($Ok.Trim().Length -gt 0) {
  $Ok.Split(",") | ForEach-Object {
    $v = $_.Trim()
    if ($v) { [void]$okSet.Add([int]$v) }
  }
}

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$lines = Get-Content $manifest | Select-Object -Skip 1

$wOk = 0; $wNo = 0
foreach ($ln in $lines) {
  $p = $ln.Split("`t")
  if ($p.Count -lt 4) { continue }

  $idx = [int]$p[0]
  $score = $p[1]
  $orig = $p[3]

  $label = $(if ($okSet.Contains($idx)) { "OK" } else { "NO" })
  ("{0}`t{1}`t{2}`t{3}" -f $now, $label, $score, $orig) | Add-Content -Encoding UTF8 $fb
  if ($label -eq "OK") { $wOk++ } else { $wNo++ }
}

Log ("Saved feedback: OK=" + $wOk + " NO=" + $wNo)
Log ("Feedback file: " + $fb)

# run python updater
$upd = Join-Path $PSScriptRoot "taste_update_b.py"
if (-not (Test-Path $upd)) { throw "missing updater: $upd" }

Log "Running taste_update_b.py ..."
& $cfg.python_exe -u $upd
