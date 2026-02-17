$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== TG PIPELINE RUN ALL ===" -ForegroundColor Cyan

# ---------- INGEST ----------
Write-Host "`n[1/9] Meme ingest..."
python scripts\tg_meme_ingest.py

Write-Host "`n[2/9] Video ingest 24h..."
python tg_ingest_24h.py

# ---------- A-VIDEO RANK ----------
Write-Host "`n[3/9] A-video rank 24h..."
python scripts\a_rank_video_24h.py

# ---------- AUDIO/SILENCE FILTER (A-video + B) ----------
Write-Host "`n[4/9] Filter audio/silence (A-video + B)..."
$env:REQUIRE_AUDIO="1"
$env:SILENCE_SEC="15"
$env:SILENCE_DB="-35"
$env:SILENCE_DUR="0.4"
$env:SILENCE_PCT="70"

python scripts\filter_audio_silence_jsonl.py out\reports\a_rank_video_24h.jsonl out\reports\a_rank_video_24h_audio.jsonl
python scripts\filter_audio_silence_jsonl.py out\reports\b_rank_mix.jsonl       out\reports\b_rank_mix_audio.jsonl

# ---------- A-VIDEO REVIEW ----------
Write-Host "`n[5/9] A-video review (оценка вкуса)..."
python scripts\av_next_review.py
Start-Process "http://127.0.0.1:8011"
python scripts\av_review_server.py

# ---------- A-VIDEO SMART ----------
Write-Host "`n[6/9] A-video smart pick..."
python scripts\av_pick_smart.py

# ---------- MEME SMART + FRESH FILTER ----------
Write-Host "`n[7/10] Update meme banned channels (3/30)...
$env:A_CH_BAN_WINDOW="30"
$env:A_CH_BAN_THRESH="3"
python scripts\m_update_banned_channels.py

[8/10] Meme smart..."
python scripts\m_next_smart.py

Write-Host "`n[8/9] Meme fresh filter (drop archive)..."
$env:MAX_AGE_HOURS="96"
python scripts\m_filter_fresh_in_review.py

# ---------- UNIFIED ----------
Write-Host "`n[9/9] Unified..."
python scripts\unified_batch.py

$day = Get-Date -Format "yyyy-MM-dd"
$index = ".\out\batches\unified\$day\index.html"

Write-Host "`nOPEN => $index" -ForegroundColor Green
ii $index
