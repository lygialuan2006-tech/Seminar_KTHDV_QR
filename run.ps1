<#
One-click demo runner.
    .\run.ps1                  # port 8003 + ngrok
    .\run.ps1 -Port 8004
    .\run.ps1 -NoNgrok
#>
param(
    [int]$Port = 8003,
    [switch]$NoNgrok
)

$ErrorActionPreference = "Stop"
$demo = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "demo"
$py = Join-Path $demo ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    python -m venv (Join-Path $demo ".venv")
    & $py -m pip install -q -r (Join-Path $demo "requirements.txt")
}

Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 700

Set-Location $demo
& $py -m alembic upgrade head
$env:PORT = "$Port"

Write-Host "`n  Local : http://localhost:$Port`n  Login : admin / lygialuan12345`n  Stop  : Ctrl+C`n" -ForegroundColor Cyan
if ($NoNgrok) { & $py app.py } else { & $py app.py --ngrok }
