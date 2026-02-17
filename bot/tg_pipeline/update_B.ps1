param(
  [double]$Thresh = 0.84
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "[update_B] root=$root"
Write-Host "[update_B] thresh=$Thresh"

# 1) Rank MIX (live jsonl)
& $py .\tg_rank_b_clip_mix_fast_live.py

# 2) Rebuild TOP HTML from jsonl (uses make_b_top_from_jsonl.py)
# ensure THRESH in script
& $py -c "import pathlib,re; p=pathlib.Path('make_b_top_from_jsonl.py'); s=p.read_text(encoding='utf-8'); s=re.sub(r'THRESH\s*=\s*0\.\d+','THRESH = %.2f'%($Thresh),s); p.write_text(s,encoding='utf-8')"
& $py .\make_b_top_from_jsonl.py

# 3) Export candidates (hardlinks)
& $py .\export_b_candidates.py

# 4) Dedup candidates
& $py .\dedup_mvp.py --root .\data\tg\raw\B_candidates --db .\out\index\dedup_b_candidates.sqlite
& $py .\make_b_candidates_canonical.py

# 5) Rebuild queue HTML
& $py .\make_b_queue_html.py

Write-Host "[update_B] DONE"
