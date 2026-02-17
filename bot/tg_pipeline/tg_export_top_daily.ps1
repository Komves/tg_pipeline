param(
  [int]$TopN = 10,
  [ValidateSet("hardlink","copy")]
  [string]$Mode = "hardlink"
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

# optional map from latest run
$mapPath = Join-Path $cfg.out_dir "tmp\mix_new_run_map.tsv"
$map = @{}
if (Test-Path $mapPath) {
  Get-Content -Encoding UTF8 $mapPath | Select-Object -Skip 1 | ForEach-Object {
    $p = $_ -split "`t", 2
    if ($p.Count -eq 2) { $map[$p[0]] = $p[1] }
  }
  Log ("Loaded map: " + $map.Count + " rows from " + $mapPath)
} else {
  Log ("No map file: " + $mapPath)
}

$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$deliverDir = Join-Path $cfg.out_dir ("deliveries\B_" + $stamp)
New-Item -ItemType Directory -Force -Path $deliverDir | Out-Null

$rows = Get-Content -Encoding UTF8 $rankJsonl |
  Where-Object { $_ -and $_.Trim() -ne "" } |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.status -eq "ok" -and $_.score -ne $null } |
  Sort-Object score -Descending |
  Select-Object -First $TopN

if ($rows.Count -eq 0) {
  Log "No scored rows found."
  exit 0
}

$manifest = Join-Path $deliverDir "manifest.tsv"
"idx`tscore`tfile`toriginal_path" | Set-Content -Encoding UTF8 $manifest

$captions = Join-Path $deliverDir "CAPTIONS.txt"
("B TOP " + $stamp) | Set-Content -Encoding UTF8 $captions
Add-Content -Encoding UTF8 $captions ""
Add-Content -Encoding UTF8 $captions "Send B_*.mp4 to Telegram (Saved Messages)."
Add-Content -Encoding UTF8 $captions "Then apply: tg_apply_feedback.ps1 -DeliveryDir <dir> -Ok '1,3,7'"
Add-Content -Encoding UTF8 $captions ""

$ok = 0
$miss = 0

for ($i=0; $i -lt $rows.Count; $i++) {
  $r = $rows[$i]
  $idx = $i + 1
  $score = [double]$r.score

  $p = [string]$r.path
  $resolved = $null

  if ($p -and (Test-Path $p)) {
    $resolved = $p
  } else {
    # try tmp_path -> map
    $tp = $null
    try { $tp = [string]$r.tmp_path } catch {}
    if ($tp -and $map.ContainsKey($tp) -and (Test-Path $map[$tp])) {
      $resolved = $map[$tp]
    } elseif ($p -and $map.ContainsKey($p) -and (Test-Path $map[$p])) {
      $resolved = $map[$p]
    }
  }

  if (-not $resolved) {
    $miss++
    Log ("⚠️ missing source for idx=" + $idx + " path=" + $p)
    continue
  }

  $dstName = ("B_{0:D2}_s{1}.mp4" -f $idx, ("{0:N4}" -f $score).Replace(",", "."))
  $dst = Join-Path $deliverDir $dstName

  if ($Mode -eq "hardlink") {
    New-Item -ItemType HardLink -Path $dst -Target $resolved | Out-Null
  } else {
    Copy-Item -Force $resolved $dst
  }

  ("{0}`t{1:N4}`t{2}`t{3}" -f $idx, $score, $dstName, $resolved) | Add-Content -Encoding UTF8 $manifest
  Add-Content -Encoding UTF8 $captions ("{0}) {1}  score={2:N4}" -f $idx, $dstName, $score)
  $ok++
}

Log ("✅ Delivery folder: " + $deliverDir)
Log ("exported=" + $ok + " missing=" + $miss)
Log ("📄 manifest: " + $manifest)
Log ("📄 captions: " + $captions)

