$root="C:\Users\Марк\tg_pipeline\tg_pipeline"
$logs=Join-Path $root "out\logs"
$fixed=Join-Path $env:USERPROFILE "Downloads\a_feedback_fixed.tsv"

python (Join-Path $root "out\tmp\fix_feedback.py")

$n=(Get-ChildItem $logs -Filter "a_feedback_*.tsv").Count + 1
$dst=Join-Path $logs ("a_feedback_{0:D3}.tsv" -f $n)
Copy-Item $fixed $dst -Force
Write-Host "ADDED ->" $dst

python (Join-Path $root "out\tmp\rebuild_master_canon.py")

$master=Join-Path $logs "a_feedback_master.tsv"
python -c "import re; ok=no=0
for l in open(r'$master',encoding='utf-8',errors='ignore'):
  if re.search(r'(^|\s)OK(\s|$)',l): ok+=1
  elif re.search(r'(^|\s)NO(\s|$)',l): no+=1
print('MASTER OK',ok,'NO',no,'TOTAL',ok+no)"

python (Join-Path C:\Users\Марк\tg_pipeline\tg_pipeline 'out\tmp\build_a_profile_from_feedback.py')

