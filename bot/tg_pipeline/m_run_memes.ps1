$ErrorActionPreference="Stop"
cd "C:\Users\Марк\tg_pipeline\tg_pipeline"

Write-Host "=== MEME REVIEW RUN ==="

# 0) убить того, кто слушает 8765
$line = (netstat -ano | Select-String ":8765\s+LISTENING" | Select-Object -First 1)
if ($line) {
  $pid = ($line.ToString().Trim() -split "\s+")[-1]
  if ($pid -match "^\d+$") {
    Write-Host "Killing old server PID=$pid"
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
  }
}

# 1) окно свежести
$env:MAX_AGE_HOURS = "48"
$env:M_TARGET = "70"

Write-Host "Ingest memes..."
python scripts\tg_meme_ingest.py

Write-Host "Build smart batch..."
python scripts\m_next_smart.py

Write-Host "Filter (fresh + bans + dedupe + fallback)..."
python scripts\m_filter_fresh_in_review.py

Write-Host "Fill from fresh RAW if batch too small..."
python scripts\m_fill_from_fresh_raw.py

Write-Host "Update banned-items/channels from feedback, then re-filter..."
python scripts\m_update_banned_items.py
python scripts\m_update_banned_channels.py
python scripts\m_filter_fresh_in_review.py

Write-Host "Starting server..."
Start-Process powershell -ArgumentList '-NoExit','-Command','cd "C:\Users\Марк\tg_pipeline\tg_pipeline"; python scripts\m_review_server.py'

# ждать порт
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 250
  if (netstat -ano | Select-String ":8765\s+LISTENING") { break }
}

Write-Host "Opening browser..."
Start-Process "http://127.0.0.1:8765/index.html"
