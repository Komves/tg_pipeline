param(
  [switch]$NoSinceLastRun,
  [int]$TopN = 10,
  [double]$Thresh = 0.84,
  [switch]$ForceAll
)

$ErrorActionPreference = "Stop"

function Log([string]$msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host ("[$ts] " + $msg)
}

function Kill-LeftoverRankers {
  # Kills only python processes whose command line contains rank_b_new_tmp.py
  try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*rank_b_new_tmp.py*"
    }
    foreach ($p in $procs) {
      Log ("⚠️ killing leftover ranker python pid=" + $p.ProcessId)
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  } catch {
    # If CIM not available, do nothing (but this is rare on Windows)
    Log ("⚠️ could not query Win32_Process: " + $_.Exception.Message)
  }
}

function Remove-FileSafe($path) {
  # Remove with retries (handles brief locks)
  for ($i=1; $i -le 5; $i++) {
    try {
      if (Test-Path $path) { Remove-Item -Force $path; return }
      return
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  throw "failed to remove (locked?): $path"
}

function Build-HtmlReport([object[]]$rows, [string]$outPath, [double]$thr, [int]$topN, [int]$inputs) {
  $total = @($rows).Count
  $kept  = @($rows | Where-Object { $_.score -ge $thr }).Count
  $top   = $rows | Sort-Object score -Descending | Select-Object -First $topN
  $now   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

  $head = @"
<!doctype html>
<html><head><meta charset="utf-8">
<title>B Daily Top</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif; margin:24px;}
h1{margin:0 0 8px 0;}
.meta{color:#555; margin-bottom:16px;}
table{border-collapse:collapse; width:100%;}
th,td{border:1px solid #ddd; padding:8px; font-size:14px;}
th{text-align:left; background:#f3f3f3;}
.score{font-variant-numeric: tabular-nums;}
.path{font-family:Consolas,monospace; font-size:12px; word-break:break-all;}
</style></head><body>
<h1>TG Content Engine — B Daily Top</h1>
<div class="meta">Generated: $now<br/>Total scored: $total<br/>Kept (score ≥ $thr): $kept<br/>Inputs ranked: $inputs</div>
<table>
<tr><th>#</th><th>score</th><th>path</th></tr>
"@

  $body = ""
  $i=0
  foreach ($r in $top) {
    $i++
    $score = "{0:N4}" -f $r.score
    $pathEsc = [System.Web.HttpUtility]::HtmlEncode([string]$r.path)
    $body += "<tr><td>$i</td><td class='score'>$score</td><td class='path'>$pathEsc</td></tr>`n"
  }

  $tail = @"
</table>
</body></html>
"@

  ($head + $body + $tail) | Set-Content -Encoding UTF8 $outPath
  return @{ total=$total; kept=$kept }
}

# --- config
$cfgPath = Join-Path $PSScriptRoot "out\config\engine.local.json"
$cfg = Get-Content $cfgPath | ConvertFrom-Json

# --- dirs
$indexDir   = $cfg.index_dir
$reportsDir = $cfg.reports_dir
$tmpDir     = Join-Path $cfg.out_dir "tmp"
$tmpMix     = Join-Path $tmpDir "mix_new_run"
$mapPath = Join-Path $tmpDir "mix_new_run_map.tsv"

New-Item -ItemType Directory -Force -Path $indexDir, $reportsDir, $tmpDir, $tmpMix | Out-Null

# --- index files
$allPath  = Join-Path $indexDir "mix_all_mp4.txt"
$prevPath = Join-Path $indexDir "mix_prev_mp4.txt"
$newPath  = Join-Path $indexDir "mix_new_mp4.txt"

# --- report files
$outJsonl = Join-Path $reportsDir "b_rank_daily.jsonl"
$tmpJsonl = $outJsonl + ".tmp"
$outHtml  = Join-Path $reportsDir "b_top_daily.html"
$runLog   = Join-Path $reportsDir "run_log.tsv"

# --- Step A: scan ALL mp4 in MIX
Log "INGEST: scanning MIX for *.mp4 ..."
$all = Get-ChildItem -Path $cfg.tg_mix_dir -Recurse -File -Filter *.mp4 |
       Select-Object -ExpandProperty FullName |
       Sort-Object
$mixTotal = $all.Count
$all | Set-Content -Encoding UTF8 $allPath

# --- optional: filter by last successful run time (run_log.tsv)
if (-not $NoSinceLastRun -and -not $ForceAll -and (Test-Path $runLog)) {
  try {
    $last = Get-Content $runLog | Select-Object -Last 1
    if ($last -and $last -notmatch "^ts	") {
      $tsStr = ($last -split "	")[0]
      $lastTs = [datetime]::ParseExact($tsStr, "yyyy-MM-dd HH:mm:ss", $null)
      Log ("⏱️ SinceLastRun: filtering by LastWriteTime > " + $lastTs.ToString("yyyy-MM-dd HH:mm:ss"))

      $recent = @()
      foreach ($fp in $all) {
        try {
          $fi = Get-Item $fp
          if ($fi.LastWriteTime -gt $lastTs) { $recent += $fp }
        } catch {}
      }
      $all = $recent | Sort-Object
      Log ("⏱️ SinceLastRun: remaining after time-filter = " + $all.Count)
    }
  } catch {
    Log ("⚠️ SinceLastRun parse failed: " + $_.Exception.Message)
  }
}

if (-not (Test-Path $prevPath)) { "" | Set-Content -Encoding UTF8 $prevPath }
$prev = Get-Content $prevPath

# --- Step B: NEW filter
$new = $all | Where-Object { $_ -notin $prev }
$new | Set-Content -Encoding UTF8 $newPath
if (-not $ForceAll) {
  Copy-Item -Force $allPath $prevPath
} else {
  Log "⚙️ ForceAll: not updating mix_prev_mp4.txt snapshot"
}

Log ("🆕 NEW: {0} (mix_total={1}, after_time_filter={2})" -f $new.Count, $mixTotal, $all.Count)

if ($ForceAll) {
  Log "⚙️ ForceAll: ranking ALL mp4 from MIX snapshot"
  $new = $all
}
if ($new.Count -eq 0 -and -not $ForceAll) {
  Log "Nothing new. Exiting."
  exit 0
}


# --- Step C: materialize NEW as hardlinks into tmpMix
Log "LINK: building hardlinks set..."
Remove-Item -Force $mapPath -ErrorAction SilentlyContinue
"tmp_path`toriginal_path" | Set-Content -Encoding UTF8 $mapPath
Get-ChildItem -Path $tmpMix -Force -ErrorAction SilentlyContinue | Remove-Item -Force -Recurse -ErrorAction SilentlyContinue

$i = 0
foreach ($src in $new) {
  $i++
  $dst = Join-Path $tmpMix ("new_{0:D5}.mp4" -f $i)
  New-Item -ItemType HardLink -Path $dst -Target $src | Out-Null
("{0}`t{1}" -f $dst, $src) | Add-Content -Encoding UTF8 $mapPath
}
Log ("⛓️ hardlinks ready: {0}" -f $new.Count)

# --- Step D: patch ranker script to use tmpMix + atomic tmpJsonl
$rankTmp = Join-Path $tmpDir "rank_b_new_tmp.py"

$srcText = Get-Content $cfg.rank_script_b -Raw
$projEsc    = ($cfg.project_root -replace "\\","\\")
$tmpMixEsc  = ($tmpMix -replace "\\","\\")
$tmpJsonEsc = ($tmpJsonl -replace "\\","\\")
$outHtmlEsc = ($outHtml -replace "\\","\\")

$srcText = $srcText -replace '(?m)^\s*ROOT\s*=.*$', ("ROOT = Path(r`"{0}`")" -f $projEsc)
$srcText = $srcText -replace 'IN_DIR\s*=\s*ROOT\s*/\s*r"data\\tg\\raw\\MIX"', ("IN_DIR = Path(r`"{0}`")" -f $tmpMixEsc)
$srcText = $srcText -replace 'OUT_JSONL\s*=\s*ROOT\s*/\s*r"out\\reports\\b_rank_okprofile_full\.jsonl"', ("OUT_JSONL = Path(r`"{0}`")" -f $tmpJsonEsc)
$srcText = $srcText -replace 'OUT_HTML\s*=\s*ROOT\s*/\s*r"out\\reports\\b_top_okprofile_full\.html"', ("OUT_HTML  = Path(r`"{0}`")" -f $outHtmlEsc)

Set-Content -Encoding UTF8 -Path $rankTmp -Value $srcText

# --- Step E: run ranker with heartbeat + safe-kill leftovers
Kill-LeftoverRankers
Remove-FileSafe $tmpJsonl

Log "RANK: start"
$rankOut = Join-Path $tmpDir "rank_stdout.log"
$rankErr = Join-Path $tmpDir "rank_stderr.log"
Remove-FileSafe $rankOut
Remove-FileSafe $rankErr

$start = Get-Date
$p = Start-Process -FilePath $cfg.python_exe -ArgumentList @("-u", $rankTmp) -NoNewWindow -PassThru `
                  -RedirectStandardOutput $rankOut -RedirectStandardError $rankErr

$lastLen = -1
$sameCount = 0

while (-not $p.HasExited) {
  Start-Sleep -Seconds 30
  $elapsed = [int]((Get-Date) - $start).TotalSeconds

  $tmpLen = $(if (Test-Path $tmpJsonl) { (Get-Item $tmpJsonl).Length } else { 0 })
  $outLen = $(if (Test-Path $rankOut) { (Get-Item $rankOut).Length } else { 0 })
  $errLen = $(if (Test-Path $rankErr) { (Get-Item $rankErr).Length } else { 0 })

  Log ("…rank heartbeat: {0}s elapsed | tmp={1}B | stdout={2}B | stderr={3}B" -f $elapsed, $tmpLen, $outLen, $errLen)

  if ($tmpLen -eq $lastLen) { $sameCount++ } else { $sameCount = 0; $lastLen = $tmpLen }

  if ($sameCount -ge 6) {
    Log "⚠️ rank seems stuck: tmp size not growing for ~3 minutes"
    Log ("📄 stdout log: " + $rankOut)
    Log ("📄 stderr log: " + $rankErr)
    if (Test-Path $rankErr) {
      Log "— stderr tail (20 lines) —"
      Get-Content $rankErr -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Log $_ }
    }
    if (Test-Path $rankOut) {
      Log "— stdout tail (20 lines) —"
      Get-Content $rankOut -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Log $_ }
    }
    $sameCount = 0
  }
}

$p.WaitForExit()
$exitCode = $p.ExitCode
Log ("RANK: finished (exit={0})" -f $exitCode)

if (-not (Test-Path $tmpJsonl)) { throw "missing tmp jsonl: $tmpJsonl" }

# --- Step F: commit JSONL (tmp -> final)
if (Test-Path $outJsonl) { Remove-Item -Force $outJsonl }
Rename-Item -Force -Path $tmpJsonl -NewName (Split-Path $outJsonl -Leaf)
$committedLines = (Get-Content $outJsonl | Measure-Object -Line).Lines
Log ("✅ jsonl committed lines: " + $committedLines)

# --- normalize JSONL paths: tmp hardlink -> original path (using map)
if (Test-Path $mapPath) {
  Log ("🧭 normalize paths using map: " + $mapPath)
  $map = @{}
  Get-Content $mapPath | Select-Object -Skip 1 | ForEach-Object {
    $p = $_ -split "`t", 2
    if ($p.Count -eq 2) { $map[$p[0]] = $p[1] }
  }
  $tmpFixed = $outJsonl + ".normtmp"
  $changed = 0
  Get-Content -Encoding UTF8 $outJsonl | ForEach-Object {
    $line = $_
    try {
      $o = $line | ConvertFrom-Json
      if ($o.path -and $map.ContainsKey([string]$o.path)) {
        $o | Add-Member -NotePropertyName tmp_path -NotePropertyValue ([string]$o.path) -Force
        $o.path = $map[[string]$o.path]
        $changed++
        $line = ($o | ConvertTo-Json -Compress)
      }
    } catch {}
    $line | Add-Content -Encoding UTF8 $tmpFixed
  }
  Remove-Item -Force $outJsonl
  Rename-Item -Force $tmpFixed -NewName (Split-Path $outJsonl -Leaf)
  Log ("🧭 normalized paths changed=" + $changed)
}

# archive per-run
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$stamp = $stamp -replace " ", ""

Copy-Item -Force $outJsonl $archJsonl
Log ("🗄️ archived jsonl: " + $archJsonl)

# --- Step G: build HTML + metrics
$rows = Get-Content $outJsonl | Where-Object { $_ -and $_.Trim() -ne "" } | ForEach-Object { $_ | ConvertFrom-Json }
$metrics = Build-HtmlReport -rows $rows -outPath $outHtml -thr $Thresh -topN $TopN -inputs $new.Count

Log ("✅ html: " + $outHtml)
Log ("📊 total={0} kept>={1}={2} topN={3}" -f $metrics.total, $Thresh, $metrics.kept, $TopN)

# --- Step H: append run log
if (-not (Test-Path $runLog)) {
  "ts`tmode`tinputs`tjsonl_lines`tkept`tarch_jsonl" | Set-Content -Encoding UTF8 $runLog
}
$mode = $(if ($ForceAll) { "ALL" } else { "NEW" })
$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
("{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $now, $mode, $new.Count, $metrics.total, $metrics.kept, $archJsonl) | Add-Content -Encoding UTF8 $runLog
Log ("🧾 log: " + $runLog)





