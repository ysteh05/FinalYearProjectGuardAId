param(
    [string]$FirebaseCredentials = $env:GUARDAID_FIREBASE_CREDENTIALS,
    [switch]$Maintenance,
    [int]$Port = 8020
)
$ErrorActionPreference = 'Stop'
$monitorRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$projectRoot = (Resolve-Path (Join-Path $monitorRoot '..')).Path
$runtimePython = 'C:\tmp\guardaid-vlm-venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $runtimePython)) { throw 'Project Python runtime is missing.' }
Push-Location -LiteralPath $monitorRoot
try {
    $schemaVersionOutput = & $runtimePython -c 'from app.schema_v20 import VERSION; print(VERSION)'
    if ($LASTEXITCODE -ne 0 -or $schemaVersionOutput -notmatch '^\d+$') {
        throw 'Cannot determine the current backend schema version.'
    }
    $expectedSchemaVersion = [int]$schemaVersionOutput
} finally {
    Pop-Location
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Inspect the existing service before starting another."
}
if ([string]::IsNullOrWhiteSpace($FirebaseCredentials)) {
    $securePath = Join-Path $env:SystemDrive 'secure\guardaid-secrets'
    $candidates = @(Get-ChildItem -LiteralPath $securePath -File -Filter '*.json' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'firebase-adminsdk|service-account' })
    if ($candidates.Count -ne 1) { throw 'Supply the repository-external Firebase service-account path explicitly.' }
    $FirebaseCredentials = $candidates[0].FullName
}
$clientPath = Join-Path $projectRoot 'mobileapp\android\app\google-services.json'
$clientConfig = Get-Content -LiteralPath $clientPath -Raw | ConvertFrom-Json
$serviceConfig = Get-Content -LiteralPath $FirebaseCredentials -Raw | ConvertFrom-Json
$projectId = [string]$clientConfig.project_info.project_id
if ([string]::IsNullOrWhiteSpace($projectId) -or $serviceConfig.project_id -ne $projectId) {
    throw 'Firebase client and service-account project IDs do not match.'
}
$env:GUARDAID_PERSISTENCE_MODE = 'sqlite'
$env:GUARDAID_AUTH_MODE = 'firebase'
$env:GUARDAID_NOTIFICATION_MODE = 'firebase'
$env:GUARDAID_FIREBASE_PROJECT_ID = $projectId
$env:GUARDAID_FIREBASE_CREDENTIALS = (Resolve-Path -LiteralPath $FirebaseCredentials).Path
$env:GUARDAID_DATABASE_PATH = Join-Path $monitorRoot 'runtime_data\database\guardaid.db'
$env:GUARDAID_DATA_DIR = Join-Path $monitorRoot 'runtime_data'
$env:GUARDAID_MAINTENANCE_MODE = if ($Maintenance) { '1' } else { '0' }
if (-not $env:GUARDAID_OLLAMA_URL) { $env:GUARDAID_OLLAMA_URL = 'http://127.0.0.1:11434' }
$env:GUARDAID_WEAPON_CHECKPOINT = Join-Path $projectRoot 'model_runs\yolov9_ten_sources\runs\tenft_20261017_034950_424d7a\checkpoint_evaluations\epoch_10_20261017_102213\epoch_10_inference.pt'
$env:GUARDAID_WEAPON_THRESHOLDS = Join-Path $projectRoot 'model_runs\yolov9_ten_sources\runs\tenft_20261017_034950_424d7a\checkpoint_evaluations\epoch_10_20261017_102213\sensitivity_thresholds.json'
$packagesRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
$ffmpegDll = Get-ChildItem -LiteralPath $packagesRoot -Directory -Filter 'Gyan.FFmpeg.Shared*' -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -Filter 'avcodec-*.dll' -ErrorAction SilentlyContinue } |
    Select-Object -First 1
if ($ffmpegDll) { $env:PATH = "$($ffmpegDll.DirectoryName);$env:PATH" }
$logs = Join-Path $monitorRoot 'runtime_data\logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$runId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $logs "backend-$runId.stdout.log"
$stderr = Join-Path $logs "backend-$runId.stderr.log"
if (!$Maintenance) {
    $cutoverState = Join-Path $projectRoot '.db_upgrade_work\cutover_state.json'
    if (Test-Path -LiteralPath $cutoverState) {
        $cutover = Get-Content -LiteralPath $cutoverState -Raw | ConvertFrom-Json
        $cutover.service_resumed = $true
        $cutover.stage = 'service_resume_requested'
        $cutover | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $cutoverState -Encoding utf8
    }
}
$backend = Start-Process -FilePath $runtimePython -WorkingDirectory $monitorRoot `
    -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port") `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$deadline = (Get-Date).AddSeconds(120)
$health = $null
do {
    Start-Sleep -Milliseconds 500
    if ($backend.HasExited) { throw "Backend exited; inspect $stderr" }
    try { $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 } catch { $health = $null }
} while ($null -eq $health -and (Get-Date) -lt $deadline)
if ($null -eq $health -or $health.schemaVersion -ne $expectedSchemaVersion -or $health.maintenanceMode -ne [bool]$Maintenance) {
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    throw 'Backend health/schema/maintenance verification failed.'
}
$servicePid = @(Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -ExpandProperty OwningProcess -Unique)
if ($servicePid.Count -ne 1) { throw 'Cannot identify the backend listener process.' }
$servicePid = [int]$servicePid[0]
if ($servicePid -ne $backend.Id) {
    $serviceInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid"
    if ($serviceInfo.ParentProcessId -ne $backend.Id) { throw 'Backend listener is not owned by the launched Python process.' }
}
$serviceProcess = Get-Process -Id $servicePid
$state = [ordered]@{ pid=$servicePid; launcherPid=$backend.Id; startedAt=$serviceProcess.StartTime.ToUniversalTime().ToString('o');
    port=$Port; schemaVersion=$expectedSchemaVersion; maintenanceMode=[bool]$Maintenance; authMode='firebase'; notificationMode='firebase';
    stdout=$stdout; stderr=$stderr }
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logs 'backend-current.json') -Encoding utf8
$state | ConvertTo-Json
