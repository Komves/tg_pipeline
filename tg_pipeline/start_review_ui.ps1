param([int]$TopN = 10, [int]$Port = 8787)
$ErrorActionPreference = "Stop"
$cfg = Get-Content (Join-Path $PSScriptRoot "out\config\engine.local.json") | ConvertFrom-Json
$py = $cfg.python_exe
$server = Join-Path $PSScriptRoot "review_ui_server.py"
$u = "http://127.0.0.1:$Port/"
Write-Host "Starting Review UI at $u"
Start-Process $u
& $py -u $server --topn $TopN --port $Port
