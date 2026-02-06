param([int]$N = 70)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path ".").Path
$tmp  = Join-Path $root "out\tmp\m_review_local"
$mix  = Join-Path $root "data\tg\raw\MIX"
$exts = @("*.jpg","*.jpeg","*.png","*.webp","*.gif")

$blPath = Join-Path $root "out\config\m_blacklist_paths.txt"
$blPat  = Join-Path $root "out\config\m_blacklist_patterns.txt"

# load blacklist paths
$blset = New-Object "System.Collections.Generic.HashSet[string]"
if(Test-Path $blPath){
  Get-Content $blPath -Encoding UTF8 | ForEach-Object {
    $t=$_.Trim()
    if($t -and -not $t.StartsWith("#")){ [void]$blset.Add($t.ToLower()) }
  }
}

# load patterns (substring match)
$pats = @()
if(Test-Path $blPat){
  $pats = Get-Content $blPat -Encoding UTF8 | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") }
}

# очистка tmp
Get-ChildItem $tmp -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notin @("index.html","data.js") } |
  Remove-Item -Force -ErrorAction SilentlyContinue

# собрать все картинки
$all = @()
foreach($e in $exts){ $all += Get-ChildItem $mix -Recurse -File -Filter $e -ErrorAction SilentlyContinue }
if($all.Count -eq 0){ Write-Host "NO MEME IMAGES FOUND in $mix"; exit 1 }

# apply filters
$pool = $all | Where-Object {
  $p = $_.FullName
  if($blset.Contains($p.ToLower())){ return $false }
  foreach($pat in $pats){
    if($p.ToLower().Contains($pat.ToLower())){ return $false }
  }
  return $true
}

if($pool.Count -lt 10){ $pool = $all }

$pick = $pool | Get-Random -Count ([Math]::Min($N, $pool.Count))

$items = @()
$k = 1
foreach($f in $pick){
  $dstName = ("m_{0:000}" -f $k) + $f.Extension.ToLower()
  Copy-Item $f.FullName (Join-Path $tmp $dstName) -Force
  $items += @{ src = $dstName; orig = $f.FullName; label = "" }
  $k++
}

$batchId = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$content = "window.BATCH_ID = $batchId;`nwindow.BATCH_ITEMS = " + ($items | ConvertTo-Json -Depth 6) + ";"
$content | Set-Content -Encoding UTF8 (Join-Path $tmp "data.js")

Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-Command","py scripts\m_review_server.py"
Start-Sleep -Milliseconds 500
Start-Process ("http://127.0.0.1:8765/index.html?v=$batchId")

Write-Host "BATCH READY: $($items.Count) memes (paths=$($blset.Count), patterns=$($pats.Count))"
