param(
    [int]$Port = 8020
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExistingPython = "C:\tmp\guardaid-vlm-venv\Scripts\python.exe"
$LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
} elseif (Test-Path -LiteralPath $ExistingPython) {
    $Python = $ExistingPython
} else {
    throw "No GuardAId Python environment found. Follow README.md to create .venv first."
}

$env:GUARDAID_PERSISTENCE_MODE = "sqlite"
$env:GUARDAID_AUTH_MODE = "disabled"
$env:GUARDAID_NOTIFICATION_MODE = "console"
Set-Location -LiteralPath $ProjectRoot
$LogDirectory = Join-Path $ProjectRoot "runtime_data\logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$SessionLog = Join-Path $LogDirectory "last_server_session.log"
Start-Transcript -Path $SessionLog -Force | Out-Null
Write-Host "GuardAId local mode: SQLite and media stay on this computer." -ForegroundColor Green
Write-Host "Open http://127.0.0.1:$Port after the server starts. Press Ctrl+C to stop."
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
$ServerExitCode = $LASTEXITCODE
if ($ServerExitCode -ne 0) {
    Write-Host "GuardAId server exited unexpectedly with code $ServerExitCode." -ForegroundColor Red
    Write-Host "Diagnostic log: $SessionLog" -ForegroundColor Yellow
}
Stop-Transcript | Out-Null
exit $ServerExitCode
