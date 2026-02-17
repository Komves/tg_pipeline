param(
  [Parameter(Mandatory=$true)][int]$Id,
  [Parameter(Mandatory=$true)][ValidateSet("ok","no","later")][string]$Label
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$canon = ".\out\index\b_candidates_canonical.txt"
$log = ".\out\logs\b_feedback.tsv"

if (!(Test-Path $canon)) { throw "missing: $canon" }

$paths = Get-Content -Encoding UTF8 $canon
if ($Id -lt 1 -or $Id -gt $paths.Count) { throw "id out of range: 1..$($paths.Count)" }

$path = $paths[$Id-1].Trim()
$ts = (Get-Date).ToString("o")

New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
"$ts`t$Id`t$Label`t$path" | Add-Content -Encoding UTF8 $log

Write-Host "[marked] id=$Id label=$Label"
Write-Host "path=$path"
Write-Host "log=$log"
