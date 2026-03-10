param(
  [string]$Project = "kids_pod",
  [string]$DataDir = "cache\after_etsy_fallback_hardening",
  [int]$DedupHours = 0
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
Set-Location ..

Write-Host "[1/3] Capture Etsy hardening payload from fallback URL list..."
.\venv\Scripts\python.exe tools\platform_hardening_capture.py `
  --project $Project `
  --platform etsy `
  --from-urls-file tools\etsy_fallback_urls.txt `
  --max-results 30

Write-Host "[2/3] Run JARVIS cycle..."
.\venv\Scripts\python.exe jarvis_runner.py `
  --project $Project `
  --data-dir $DataDir `
  --dedup-hours $DedupHours

Write-Host "[3/3] Latest platform report:"
Get-ChildItem -Recurse "$DataDir\reports\$Project\*\platform_report.json" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName
