# TG CONTENT ENGINE - STATUS (Level 1: Engine)

Date: 2026-01-23
Project: C:\Users\Марк\tg_pipeline\tg_pipeline

## What it is
daily_run.ps1 implements daily pipeline:
TG MIX -> only-new -> materialize -> CLIP rank (B profile) -> reports + logs + archive.

## How to run

### Daily mode (only new)
cd C:\Users\Марк\tg_pipeline\tg_pipeline
powershell -ExecutionPolicy Bypass -File .\daily_run.ps1

### Disable time filter
powershell -ExecutionPolicy Bypass -File .\daily_run.ps1 -NoSinceLastRun

### Full rerank (debug mode)
powershell -ExecutionPolicy Bypass -File .\daily_run.ps1 -ForceAll

Important: ForceAll does NOT update mix_prev_mp4.txt snapshot.

## Inputs
TG MIX: data\tg\raw\MIX
Profile B: out\b_profile_ok.npy
Python: C:\Users\Марк\video_scan\venv\Scripts\python.exe

## Index files
out\index\mix_all_mp4.txt
out\index\mix_prev_mp4.txt
out\index\mix_new_mp4.txt

## Outputs
out\reports\b_rank_daily.jsonl
out\reports\b_top_daily.html
out\reports\b_rank_YYYY-MM-DD_HHMMSS.jsonl
out\reports\run_log.tsv

## Observability (anti-stuck)
Heartbeat every ~30 sec: tmp/stdout/stderr sizes.
Ranker logs:
out\tmp\rank_stdout.log
out\tmp\rank_stderr.log

## If tmp file is locked (ranker still running)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*rank_b_new_tmp.py*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

## Metrics
At the end:
total / kept>=THRESH / topN
JSONL contains service rows status=start/done - this is normal.
