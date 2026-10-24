$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "demo")

$launcher = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }

if (-not (Test-Path ".venv")) {
    & $launcher -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt --quiet
& (Join-Path $PSScriptRoot "demo\.venv\Scripts\alembic.exe") upgrade head

$ngrokCandidates = @()
$ngrokCandidates += Get-Command ngrok -All -CommandType Application -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -and $_.Source -notmatch '\\WindowsApps\\' } |
    ForEach-Object { $_.Source }

# Official Windows installs commonly put ngrok.exe here without adding it to PATH.
$ngrokCandidates += @(
    (Join-Path $env:LOCALAPPDATA "ngrok\ngrok.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\ngrok\ngrok.exe"),
    (Join-Path $env:ProgramFiles "ngrok\ngrok.exe"),
    (Join-Path $env:ProgramFiles "ngrok\bin\ngrok.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "ngrok\ngrok.exe")
)

$ngrok = $ngrokCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -Unique -First 1
if (-not $ngrok) {
    Write-Error "A runnable ngrok Agent was not found. Install it from https://ngrok.com/download/windows, then run this script again."
    exit 1
}
$env:NGROK_EXECUTABLE = $ngrok
try {
    & $env:NGROK_EXECUTABLE version | Out-Null
} catch {
    Write-Error "ngrok could not start at $ngrok. Reinstall the official Windows ngrok Agent, then run this script again."
    exit 1
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "ngrok could not start at $ngrok. Reinstall the official Windows ngrok Agent, then run this script again."
    exit 1
}

$ngrokMarker = Join-Path $env:LOCALAPPDATA "Seminar_QR\ngrok-auth-configured"
if (-not (Test-Path $ngrokMarker)) {
    Write-Host "First run: enter your ngrok authtoken. ngrok will store it on this computer, not in the project."
    $secureToken = Read-Host "Ngrok authtoken" -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
        & $ngrok config add-authtoken $plainToken
        if ($LASTEXITCODE -ne 0) { throw "ngrok did not accept the authtoken" }
        New-Item -ItemType Directory -Path (Split-Path $ngrokMarker) -Force | Out-Null
        New-Item -ItemType File -Path $ngrokMarker -Force | Out-Null
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
        Remove-Variable plainToken -ErrorAction SilentlyContinue
        Remove-Variable secureToken -ErrorAction SilentlyContinue
    }
}

& $python app.py
