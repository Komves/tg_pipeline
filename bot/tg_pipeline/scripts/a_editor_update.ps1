$root="C:\Users\Марк\tg_pipeline\tg_pipeline"
$master=Join-Path $root "out\logs\a_feedback_master.tsv"
$cfg=Join-Path $root "out\config"
$ban=Join-Path $cfg "a_explore_ban.txt"
$cand=Join-Path $cfg "a_whitelist_candidates.txt"
$wl=Join-Path $cfg "a_channels_whitelist.txt"
New-Item -ItemType Directory -Force -Path $cfg | Out-Null
if(!(Test-Path $ban)){ "" | Set-Content -Encoding UTF8 $ban }
if(!(Test-Path $cand)){ "" | Set-Content -Encoding UTF8 $cand }

python -c "import re,collections,os
master=r'$master'
banf=r'$ban'
candf=r'$cand'
wlf=r'$wl'

def ch(p):
  m=re.search(r'[/\\\\]MIX[/\\\\]([^/\\\\]+)[/\\\\]',p,flags=re.I)
  return m.group(1) if m else None

ok=collections.Counter(); no=collections.Counter()
for line in open(master,encoding='utf-8',errors='ignore'):
  if '.mp4' not in line.lower(): 
    continue
  parts=re.split(r'\s+',line.strip())
  if len(parts)<4: 
    continue
  p=parts[-1]
  if p.startswith(':\\'): p='C'+p
  c=ch(p)
  if not c: 
    continue
  if re.search(r'(^|\\s)OK(\\s|$)',line): ok[c]+=1
  elif re.search(r'(^|\\s)NO(\\s|$)',line): no[c]+=1

WL=set([l.strip() for l in open(wlf,encoding='utf-8',errors='ignore') if l.strip()])
BAN=set([l.strip() for l in open(banf,encoding='utf-8',errors='ignore') if l.strip()])

# HARD rules:
# 1) autoban: tot>=15 and ok==0  -> ban
# 2) candidates: ok>=3 and rate>=0.25 and tot>=8 and not in WL -> candidate
new_ban=[]
new_cand=[]
for c in set(ok)|set(no):
  tot=ok[c]+no[c]
  if tot>=15 and ok[c]==0:
    if c not in BAN and c not in WL:
      new_ban.append(c)
  rate=(ok[c]/tot) if tot else 0.0
  if c not in WL and c not in BAN and ok[c]>=3 and tot>=8 and rate>=0.25:
    new_cand.append((rate,ok[c],tot,c))

# permanent ban for obvious котики/дети/эротика by name
name_ban=[]
pat=re.compile(r'(women_charm|ytx_sex|sex|baby|kids|child|cats|dogs|animals)',re.I)
for c in set(ok)|set(no):
  if pat.search(c):
    if c not in BAN:
      name_ban.append(c)

BAN.update(new_ban); BAN.update(name_ban)
open(banf,'w',encoding='utf-8').write('\\n'.join(sorted(BAN))+'\\n')

new_cand.sort(reverse=True)
C=set([l.strip() for l in open(candf,encoding='utf-8',errors='ignore') if l.strip()])
for _,_,_,c in new_cand:
  C.add(c)
open(candf,'w',encoding='utf-8').write('\\n'.join(sorted(C))+'\\n')

print('BAN_ADDED',len(new_ban)+len(name_ban),'CAND_ADDED',len(new_cand))
print('BAN_TOTAL',len(BAN),'CAND_TOTAL',len(C))"
