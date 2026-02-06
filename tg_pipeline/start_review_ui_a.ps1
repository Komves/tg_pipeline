param([int]$TopN = 30, [int]$Port = 8793)
$ErrorActionPreference = "Stop"
$cfg = Get-Content (Join-Path $PSScriptRoot "out\config\engine.local.json") | ConvertFrom-Json
$py = $cfg.python_exe
$server = Join-Path $PSScriptRoot "review_ui_server.py"
$u = "http://127.0.0.1:$Port/"

# Tell server to use A feedback file via env var
$env:REVIEW_LABEL = "A"

Write-Host "Starting Review UI (A) at $u"
Start-Process $u
& $py -u $server --topn $TopN --port $Port
