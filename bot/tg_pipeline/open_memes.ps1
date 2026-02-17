$ErrorActionPreference="Stop"
cd "C:\Users\Марк\tg_pipeline\tg_pipeline"

# 1) убить того, кто слушает 8765
$line = (netstat -ano | Select-String ":8765\s+LISTENING" | Select-Object -First 1)
if ($line) {
  $pid = ($line.ToString().Trim() -split "\s+")[-1]
  if ($pid -match "^\d+$") {
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
  }
}

# 2) старт сервера в отдельном окне
Start-Process powershell -ArgumentList '-NoExit','-Command','cd "C:\Users\Марк\tg_pipeline\tg_pipeline"; python scripts\m_review_server.py'

# 3) ждать пока порт реально станет LISTENING
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 250
  if (netstat -ano | Select-String ":8765\s+LISTENING") { break }
}

# 4) открыть страницу
Start-Process "http://127.0.0.1:8765/index.html"
