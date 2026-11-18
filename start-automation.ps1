# Starts project-local Prefect processes. They do not restart automatically after reboot.
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$prefect=Join-Path $PSScriptRoot '.venv\Scripts\prefect.exe'
$logDir=Join-Path $PSScriptRoot 'reports\automation'
New-Item -ItemType Directory -Force $logDir | Out-Null
$env:PREFECT_HOME=Join-Path $PSScriptRoot '.prefect'
$env:PREFECT_SERVER_ANALYTICS_ENABLED='false'
$env:PREFECT_API_URL='http://127.0.0.1:4200/api'
$env:WAREHOUSE_BACKEND='duckdb'
if (Get-NetTCPConnection -LocalPort 4200 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 4200 is already in use. Inspect the existing Prefect server before starting another.'
}
$server=Start-Process -FilePath $prefect -ArgumentList @('server','start','--host','127.0.0.1','--port','4200') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'server.log') -RedirectStandardError (Join-Path $logDir 'server-error.log')
$ready=$false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $null=Invoke-RestMethod -Uri "$env:PREFECT_API_URL/health" -TimeoutSec 2
        $ready=$true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw 'Prefect server did not become ready. Check reports/automation logs.' }
$runner=Start-Process -FilePath $python -ArgumentList @('flows.py','--serve') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'runner.log') -RedirectStandardError (Join-Path $logDir 'runner-error.log')
@{server_pid=$server.Id;runner_pid=$runner.Id;api=$env:PREFECT_API_URL;started_at=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $logDir 'processes.json')
Write-Output 'Prefect server and runner started. Dashboard: http://127.0.0.1:4200. Check runner.log for registration.'
