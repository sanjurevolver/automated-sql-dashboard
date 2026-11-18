param([ValidateSet('duckdb','postgres')][string]$Backend='duckdb')
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe pipeline.py --backend $Backend
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$latest=Get-Content reports/latest.json | ConvertFrom-Json
Start-Process (Join-Path $latest.report_directory 'dashboard.html')
