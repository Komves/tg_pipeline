param([int]$N=70)

$root = "C:\Users\Марк\tg_pipeline\tg_pipeline"
$mix  = Join-Path $root "data\tg\raw\MIX"
$ui   = Join-Path $root "out\tmp\a_review_local"
$logs = Join-Path $root "out\logs"
$master = Join-Path $logs "a_feedback_master.tsv"
$wl   = Join-Path $root "out\config\a_channels_whitelist.txt"

# --- 1) if downloaded feedback exists -> fix + add + rebuild master
$src = Join-Path $env:USERPROFILE "Downloads\a_feedback.tsv"
if (Test-Path $src) {
  python (Join-Path $root "out\tmp\fix_feedback.py")
  $fixed = Join-Path $env:USERPROFILE "Downloads\a_feedback_fixed.tsv"

  $n = (Get-ChildItem $logs -Filter "a_feedback_*.tsv").Count + 1
  $dst = Join-Path $logs ("a_feedback_{0:D3}.tsv" -f $n)
  Copy-Item $fixed $dst -Force
  Write-Host ("ADDED -> {0}" -f $dst)

  python (Join-Path $root "out\tmp\rebuild_master_canon.py")
}

# --- 2) build next whitelist batch
Get-ChildItem $ui -Filter "v_*" -ErrorAction SilentlyContinue | Remove-Item -Force

python -c "import os,re,json,random,subprocess,hashlib,shutil
mix=r'$mix'; ui=r'$ui'; master=r'$master'; wl=r'$wl'; N=int($N)
WH=[l.strip() for l in open(wl,encoding='utf-8',errors='ignore') if l.strip()]
def md5_1s(path):
  try:
    out=subprocess.check_output(['ffmpeg','-loglevel','error','-t','1','-i',path,'-f','md5','-'],stderr=subprocess.STDOUT)
    return hashlib.md5(out).hexdigest()
  except:
    return None
seen_full=set(); seen_name=set()
if os.path.exists(master):
  for line in open(master,encoding='utf-8',errors='ignore'):
    if '.mp4' not in line.lower(): 
      continue
    parts=re.split(r'\s+',line.strip())
    if not parts: 
      continue
    p=parts[-1].replace('\\\\','\\')
    if p.startswith(':\\'): p='C'+p
    pf=os.path.normpath(p).lower()
    seen_full.add(pf); seen_name.add(os.path.basename(pf))
cand=[]
for ch in WH:
  d=os.path.join(mix,ch)
  if not os.path.isdir(d): 
    continue
  for r,_,fs in os.walk(d):
    for fn in fs:
      if fn.lower().endswith('.mp4'):
        p=os.path.join(r,fn); pf=os.path.normpath(p).lower()
        if pf in seen_full: continue
        if os.path.basename(pf) in seen_name: continue
        cand.append(p)
random.shuffle(cand)
pick=[]; used=set()
for p in cand:
  if len(pick)>=N: break
  h=md5_1s(p)
  if not h or h in used: continue
  used.add(h); pick.append(p)
data=[]
for i,p in enumerate(pick,1):
  name=f'v_{i:03d}.mp4'
  shutil.copy2(p, os.path.join(ui,name))
  data.append({'idx':i,'score':0,'file':name,'src':p})
open(os.path.join(ui,'data.js'),'w',encoding='utf-8').write('const data = '+json.dumps(data,ensure_ascii=False)+';')
print('CAND',len(cand),'PICK',len(pick))"

Start-Process (Join-Path $ui "index.html")
