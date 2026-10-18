$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "demo")

$launcher = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }

if (-not (Test-Path ".venv")) {
    & $launcher -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt --quiet
& $python app.py
