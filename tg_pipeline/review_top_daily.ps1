param(
  [int]$TopN = 10
)

$ErrorActionPreference = "Stop"

function Log([string]$msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host ("[$ts] " + $msg)
}

$cfgPath = Join-Path $PSScriptRoot "out\config\engine.local.json"
$cfg = Get-Content $cfgPath | ConvertFrom-Json

$rankJsonl = Join-Path $cfg.reports_dir "b_rank_daily.jsonl"
if (-not (Test-Path $rankJsonl)) { throw "missing: $rankJsonl" }

$fb = Join-Path $cfg.logs_dir "b_feedback.tsv"
New-Item -ItemType Directory -Force -Path $cfg.logs_dir | Out-Null
if (-not (Test-Path $fb)) {
  "ts`tlabel`tscore`tpath" | Set-Content -Encoding UTF8 $fb
}

# Read top from JSONL
$rows = Get-Content $rankJsonl |
  Where-Object { $_ -and $_.Trim() -ne "" } |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.status -eq "ok" -and $_.score -ne $null } |
  Sort-Object score -Descending |
  Select-Object -First $TopN

if ($rows.Count -eq 0) {
  Log "No scored rows found in b_rank_daily.jsonl"
  exit 0
}

Log ("Reviewing TopN=" + $TopN + " from " + $rankJsonl)
Write-Host "Keys: [O]=OK  [N]=NO  [S]=skip  [Q]=quit"

$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

for ($i=0; $i -lt $rows.Count; $i++) {
  $r = $rows[$i]
  $idx = $i + 1
  $score = [double]$r.score
  $path = [string]$r.path

  Write-Host ""
  Write-Host ("#{0}/{1}  score={2:N4}" -f $idx, $rows.Count, $score)
  Write-Host $path

  while ($true) {
    $k = Read-Host "Decision"
    if (-not $k) { $k = "S" }
    $k = $k.Trim().ToUpperInvariant()

    if ($k -eq "Q") { Log "Quit."; break 2 }
    if ($k -eq "S") { break }

    if ($k -eq "O" -or $k -eq "N") {
      $label = $(if ($k -eq "O") { "OK" } else { "NO" })
      ("{0}`t{1}`t{2}`t{3}" -f $now, $label, ("{0:N4}" -f $score), $path) | Add-Content -Encoding UTF8 $fb
      Log ("Saved: " + $label + "  score=" + ("{0:N4}" -f $score))
      break
    }

    Write-Host "Use O / N / S / Q"
  }
}

Log ("Feedback file: " + $fb)

# Run python updater after review
$py = $cfg.python_exe
$upd = Join-Path $PSScriptRoot "taste_update_b.py"
if (-not (Test-Path $upd)) { throw "missing updater: $upd" }

Log "Running taste_update_b.py ..."
& $py -u $upd
