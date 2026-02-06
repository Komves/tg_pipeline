param(
  [int]$TopN = 70,
  [string]$OutDir = "out\tmp\a_review_web",
  [switch]$Hardlink
)

$ErrorActionPreference = "Stop"

$proj = "C:\Users\Марк\tg_pipeline\tg_pipeline"
$rank = Join-Path $proj "out\reports\a_rank_memes.jsonl"
if (!(Test-Path $rank)) { throw "missing rank: $rank" }

$out = Join-Path $proj $OutDir
New-Item -ItemType Directory -Force $out | Out-Null

# чистим старые v_*.mp4 чтобы не путались
Get-ChildItem $out -Filter "v_*.mp4" -File -ErrorAction SilentlyContinue | Remove-Item -Force

# 1) читаем jsonl → ok rows → topN
$items = Get-Content $rank -Encoding UTF8 | ForEach-Object {
  try {
    $o = $_ | ConvertFrom-Json
    if ($o.status -eq "ok" -and $o.score -ne $null -and $o.path) {
      [pscustomobject]@{ score=[double]$o.score; path=[string]$o.path }
    }
  } catch {}
} | Where-Object { Test-Path $_.path } | Sort-Object score -Descending | Select-Object -First $TopN

if (!$items -or $items.Count -eq 0) { throw "no ok rows parsed from $rank" }

# 2) материализуем видео
$data = @()
$i=0
foreach ($it in $items) {
  $i++
  $src = $it.path
  $dstName = ("v_{0:d3}.mp4" -f $i)
  $dst = Join-Path $out $dstName

  if ($Hardlink) {
    try { New-Item -ItemType HardLink -Path $dst -Target $src | Out-Null }
    catch { Copy-Item -Force $src $dst }
  } else {
    Copy-Item -Force $src $dst
  }

  $data += [pscustomobject]@{
    idx   = $i
    score = [math]::Round($it.score,4)
    file  = $dstName
    src   = $src
  }
}

# 3) index.html (без fetch, данные вшиты внутрь) + LocalStorage autosave
$payload = ($data | ConvertTo-Json -Depth 5)

$html = @"
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A Review (web)</title>
<style>
body{font-family:Arial;margin:16px;background:#fff}
.wrap{max-width:980px;margin:0 auto}
video{width:100%;max-height:72vh;background:#000;border-radius:14px}
button{font-size:18px;padding:10px 16px;border-radius:12px;border:1px solid #bbb;cursor:pointer}
button.ok{border-color:#2b8a3e} button.no{border-color:#c92a2a}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.small{font-size:12px;color:#555;word-break:break-all;font-family:Consolas,monospace}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0}
.sticky{position:sticky;top:0;background:#fff;padding:10px 0;border-bottom:1px solid #eee}
kbd{padding:2px 6px;border:1px solid #ccc;border-radius:6px;background:#f7f7f7}
</style>

<div class="wrap">
  <div class="sticky">
    <div class="topbar">
      <div id="meta">loading...</div>
      <div class="row">
        <button id="dl">⬇ Download a_feedback.tsv</button>
        <button id="rs">Reset</button>
      </div>
    </div>
    <div class="small">Keys: <kbd>O</kbd>=OK <kbd>N</kbd>=NO <kbd>S</kbd>=SKIP <kbd>←</kbd>/<kbd>→</kbd>=prev/next</div>
  </div>

  <div id="app"></div>
</div>

<script>
const data = $payload;

let i = 0;
const storeKey = "A_REVIEW_VOTES_v1";
const votes = JSON.parse(localStorage.getItem(storeKey) || "{}");

function save(){ localStorage.setItem(storeKey, JSON.stringify(votes)); }
function count(lbl){ return Object.values(votes).filter(x=>x===lbl).length; }

const meta = document.getElementById("meta");
const app  = document.getElementById("app");

function render(){
  if(!data || data.length===0){ meta.textContent="No items"; app.innerHTML=""; return; }

  if(i >= data.length){
    meta.innerHTML = "<b>Done</b> | OK="+count("OK")+" NO="+count("NO")+" SKIP="+count("SKIP")+" (saved)";
    app.innerHTML = "<h3>Finished.</h3><p>Click Download a_feedback.tsv</p>";
    return;
  }

  const v = data[i];
  const cur = votes[v.idx] || "—";
  meta.innerHTML = "<b>"+(i+1)+"/"+data.length+"</b> score="+v.score+" | current="+cur+" | OK="+count("OK")+" NO="+count("NO")+" SKIP="+count("SKIP")+" (autosaved)";

  app.innerHTML = `
    <video controls autoplay playsinline preload="metadata">
      <source src="${v.file}" type="video/mp4">
    </video>
    <div class="row">
      <button class="ok" id="ok">✅ OK</button>
      <button class="no" id="no">❌ NO</button>
      <button id="sk">⏭ SKIP</button>
      <button id="pv">⬅ Prev</button>
      <button id="nx">Next ➡</button>
    </div>
    <p class="small">${v.src}</p>
  `;

  document.getElementById("ok").onclick = ()=>{ votes[v.idx]="OK"; save(); i++; render(); };
  document.getElementById("no").onclick = ()=>{ votes[v.idx]="NO"; save(); i++; render(); };
  document.getElementById("sk").onclick = ()=>{ votes[v.idx]="SKIP"; save(); i++; render(); };
  document.getElementById("pv").onclick = ()=>{ i=Math.max(i-1,0); render(); };
  document.getElementById("nx").onclick = ()=>{ i=Math.min(i+1,data.length); render(); };
}

function downloadTSV(){
  const ts = new Date().toISOString().replace("T"," ").slice(0,19);
  const lines = ["ts\tlabel\tscore\tpath"];
  for(const v of data){
    const label = votes[v.idx];
    if(label !== "OK" && label !== "NO") continue;
    lines.push(ts+"\t"+label+"\t"+v.score+"\t"+v.src);
  }
  const blob = new Blob([lines.join("\n")+"\n"], {type:"text/tab-separated-values;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "a_feedback.tsv";
  document.body.appendChild(a); a.click(); a.remove();
}

document.getElementById("dl").onclick = downloadTSV;
document.getElementById("rs").onclick = ()=>{ 
  for(const k in votes) delete votes[k]; 
  save(); i=0; render(); 
};

window.addEventListener("keydown",(e)=>{
  const k = e.key.toLowerCase();
  if(k==="o"){ votes[data[i].idx]="OK"; save(); i++; render(); }
  else if(k==="n"){ votes[data[i].idx]="NO"; save(); i++; render(); }
  else if(k==="s"){ votes[data[i].idx]="SKIP"; save(); i++; render(); }
  else if(e.key==="ArrowLeft"){ i=Math.max(i-1,0); render(); }
  else if(e.key==="ArrowRight"){ i=Math.min(i+1,data.length); render(); }
});

render();
</script>
"@

Set-Content -Encoding UTF8 -Path (Join-Path $out "index.html") -Value $html
Write-Host "✅ A review web ready:" $out
Write-Host "Files:" (Get-ChildItem $out -Filter "v_*.mp4" | Measure-Object).Count
