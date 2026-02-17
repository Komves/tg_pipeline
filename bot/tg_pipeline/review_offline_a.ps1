param([int]$TopN = 20)

$cfg = Get-Content (Join-Path $PSScriptRoot "out\config\engine.local.json") | ConvertFrom-Json
$rank = Join-Path $cfg.reports_dir "b_rank_daily.jsonl"
$outDir = Join-Path $PSScriptRoot "out\review_A"

if (!(Test-Path $outDir)) { New-Item -ItemType Directory $outDir | Out-Null }

# read topN (UTF8)
$items = Get-Content -Encoding UTF8 $rank | ForEach-Object {
  $o = $_ | ConvertFrom-Json
  if ($o.status -eq "ok" -and $o.score -ne $null -and $o.path) { $o }
} | Sort-Object score -Descending | Select-Object -First $TopN

if (-not $items -or $items.Count -eq 0) { throw "No items in $rank" }

$vids = @()
$i = 0
foreach ($it in $items) {
  $i++
  $src = [string]$it.path
  if (!(Test-Path $src)) { continue }
  $name = "v_$('{0:D3}' -f $i).mp4"
  $dstVid = Join-Path $outDir $name
  Copy-Item -Force $src $dstVid
  $vids += [PSCustomObject]@{ idx=$i; file=$name; score=[double]$it.score; path=$src }
}

# write data.json next to index.html
($vids | ConvertTo-Json -Depth 4) | Set-Content -Encoding UTF8 -Path (Join-Path $outDir "data.json")

$html = @"
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A Review (memes)</title>
<style>
body { font-family: Arial, sans-serif; margin: 16px; }
.wrap { max-width: 920px; margin: 0 auto; }
video { width: 100%; max-height: 70vh; background: #000; border-radius: 10px; }
button { font-size: 18px; padding: 10px 16px; margin-right: 10px; border-radius: 10px; border: 1px solid #bbb; cursor: pointer; }
button.ok { border-color: #2b8a3e; }
button.no { border-color: #c92a2a; }
.small { font-size: 12px; color: #555; word-break: break-all; font-family: Consolas, monospace; }
.bar { display:flex; justify-content:space-between; align-items:center; gap:10px; margin: 10px 0; flex-wrap: wrap; }
</style>
</head>
<body>
<div class="wrap">
  <h2>A Review (memes)</h2>
  <div class="bar">
    <div id="meta">loading...</div>
    <div>
      <button onclick="downloadTSV()">⬇ Download feedback.tsv</button>
      <button onclick="resetAll()">Reset</button>
    </div>
  </div>
  <div id="app"></div>
  <p class="small">Tip: mark memes as OK. Everything else NO/SKIP. At end click “Download feedback.tsv”.</p>
</div>

<script>
let data = [];
let i = 0;
const votes = {}; // idx -> label

async function init() {
  try {
    const r = await fetch("./data.json", { cache: "no-store" });
    data = await r.json();
  } catch (e) {
    document.getElementById("meta").innerText = "Failed to load data.json: " + e;
    return;
  }
  render();
}

function render() {
  if (!data || data.length === 0) {
    document.getElementById("meta").innerHTML = "<b>No items.</b>";
    document.getElementById("app").innerHTML = "<p>data.json empty.</p>";
    return;
  }
  if (i >= data.length) {
    document.getElementById("meta").innerHTML = `<b>Done.</b> OK=${count('OK')} NO=${count('NO')} SKIP=${count('SKIP')}`;
    document.getElementById("app").innerHTML = `<h3>Finished.</h3><p>Click <b>Download feedback.tsv</b>.</p>`;
    return;
  }
  const v = data[i];
  const lbl = votes[v.idx] || '—';
  document.getElementById("meta").innerHTML = `<b>${i+1}/${data.length}</b> score=${v.score.toFixed(4)} | current=${lbl} | OK=${count('OK')} NO=${count('NO')} SKIP=${count('SKIP')}`;

  document.getElementById("app").innerHTML = `
    <video controls autoplay playsinline>
      <source src="${v.file}" type="video/mp4">
    </video>
    <div style="margin-top:12px;">
      <button class="ok" onclick="vote('OK')">✅ OK</button>
      <button class="no" onclick="vote('NO')">❌ NO</button>
      <button onclick="vote('SKIP')">⏭ SKIP</button>
      <button onclick="prev()">⬅ Prev</button>
      <button onclick="next()">Next ➡</button>
    </div>
    <p class="small">${v.path}</p>
  `;
}

function vote(label) { votes[data[i].idx] = label; i++; render(); }
function next() { i = Math.min(i+1, data.length); render(); }
function prev() { i = Math.max(i-1, 0); render(); }
function count(label) { return Object.values(votes).filter(x => x===label).length; }

function downloadTSV() {
  let lines = ["ts\tlabel\tscore\tpath"];
  const ts = new Date().toISOString().replace('T',' ').slice(0,19);
  for (const v of data) {
    const label = votes[v.idx] || "SKIP";
    if (label === "SKIP") continue;
    lines.push(`${ts}\t${label}\t${v.score.toFixed(4)}\t${v.path}`);
  }
  const blob = new Blob([lines.join("\n")+"\n"], {type: "text/tab-separated-values;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "a_feedback.tsv";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function resetAll() { for (const k in votes) delete votes[k]; i=0; render(); }

init();
</script>
</body>
</html>
"@

Set-Content -Encoding UTF8 -Path (Join-Path $outDir "index.html") -Value $html
Write-Host "✅ Offline A-review ready:"
Write-Host $outDir
Start-Process (Join-Path $outDir "index.html")
