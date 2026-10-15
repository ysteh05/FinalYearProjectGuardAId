[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$FirebaseCredentials = $env:GUARDAID_FIREBASE_CREDENTIALS,

    [Parameter(Mandatory = $false)]
    [string]$DeviceId,

    [Parameter(Mandatory = $false)]
    [int]$BackendStartupTimeoutSeconds = 120,

    [Parameter(Mandatory = $false)]
    [ValidateSet("debug", "release")]
    [string]$BuildMode = "debug"
)

$ErrorActionPreference = "Stop"
$monitorRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectRoot = (Resolve-Path (Join-Path $monitorRoot "..")).Path
$mobileRoot = Join-Path $projectRoot "mobileapp"
$python = "C:\tmp\guardaid-vlm-venv\Scripts\python.exe"
$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$flutter = "flutter"
$backendLogDirectory = Join-Path $monitorRoot "runtime_data\logs"
$backendStdout = Join-Path $backendLogDirectory "live-backend.stdout.log"
$backendStderr = Join-Path $backendLogDirectory "live-backend.stderr.log"
$backendProcess = $null

function Stop-Backend {
    if ($null -ne $script:backendProcess -and !$script:backendProcess.HasExited) {
        Write-Host "Stopping GuardAId backend (PID $($script:backendProcess.Id))..."
        Stop-Process -Id $script:backendProcess.Id -ErrorAction SilentlyContinue
    }
}

try {
    if (!(Test-Path -LiteralPath $python)) {
        throw "Python environment not found: $python"
    }
    if (!(Test-Path -LiteralPath $adb)) {
        throw "ADB not found: $adb. Install Android platform-tools or update the script path."
    }
    if (!(Test-Path -LiteralPath $mobileRoot)) {
        throw "Mobile app directory not found: $mobileRoot"
    }
    if ([string]::IsNullOrWhiteSpace($FirebaseCredentials)) {
        # Convenience fallback for the documented local-only location. If there
        # is more than one key, require an explicit path instead of guessing.
        $secureCredentialDirectory = Join-Path $env:SystemDrive "secure\guardaid-secrets"
        $candidates = @()
        if (Test-Path -LiteralPath $secureCredentialDirectory) {
            $candidates = @(Get-ChildItem -LiteralPath $secureCredentialDirectory -File -Filter "*.json" -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "firebase-adminsdk|service-account" })
        }
        if ($candidates.Count -eq 1) {
            $FirebaseCredentials = $candidates[0].FullName
        } elseif ($candidates.Count -eq 0) {
            throw "Firebase Admin credential JSON was not found. Pass -FirebaseCredentials with the repository-external service-account path, or set GUARDAID_FIREBASE_CREDENTIALS."
        } else {
            throw "Multiple Firebase credential JSON files were found in $secureCredentialDirectory. Pass -FirebaseCredentials explicitly; the script will not guess."
        }
    }
    if (!(Test-Path -LiteralPath $FirebaseCredentials)) {
        throw "Firebase Admin credential JSON was not found at the supplied path. Replace the example path with the actual repository-external service-account JSON path."
    }

    $googleServicesPath = Join-Path $mobileRoot "android\app\google-services.json"
    if (!(Test-Path -LiteralPath $googleServicesPath)) {
        throw "Missing local mobileapp/android/app/google-services.json. Complete Firebase Android setup first."
    }
    $firebaseConfig = Get-Content -LiteralPath $googleServicesPath -Raw | ConvertFrom-Json
    $firebaseClient = $firebaseConfig.client | Where-Object {
        $_.client_info.android_client_info.package_name -eq "com.guardaid.prototype"
    } | Select-Object -First 1
    if (-not $firebaseClient) {
        throw "google-services.json has no Android client for com.guardaid.prototype."
    }
    $firebaseApiKey = [string]$firebaseClient.api_key[0].current_key
    $firebaseAppId = [string]$firebaseClient.client_info.mobilesdk_app_id
    $firebaseSenderId = [string]$firebaseConfig.project_info.project_number
    $firebaseProjectId = [string]$firebaseConfig.project_info.project_id
    $firebaseStorageBucket = [string]$firebaseConfig.project_info.storage_bucket
    if ([string]::IsNullOrWhiteSpace($firebaseApiKey) -or
        [string]::IsNullOrWhiteSpace($firebaseAppId) -or
        [string]::IsNullOrWhiteSpace($firebaseSenderId) -or
        [string]::IsNullOrWhiteSpace($firebaseProjectId)) {
        throw "Required Firebase client fields are missing from google-services.json."
    }

    New-Item -ItemType Directory -Force -Path $backendLogDirectory | Out-Null
    $env:GUARDAID_PERSISTENCE_MODE = "sqlite"
    $env:GUARDAID_AUTH_MODE = "firebase"
    $env:GUARDAID_NOTIFICATION_MODE = "firebase"
    $env:GUARDAID_FIREBASE_PROJECT_ID = $firebaseProjectId
    $env:GUARDAID_FIREBASE_CREDENTIALS = (Resolve-Path -LiteralPath $FirebaseCredentials).Path
    $env:GUARDAID_VLM_MIN_AVAILABLE_RAM_GB = "3.0"
    $env:GUARDAID_WEAPON_CHECKPOINT = Join-Path $projectRoot "model_runs\yolov9_selected_sources_50epoch\runs\sel50_20261011_193754_83560d\best_inference.pt"
    $env:GUARDAID_WEAPON_THRESHOLDS = Join-Path $projectRoot "model_runs\yolov9_selected_sources_50epoch\runs\sel50_20261011_193754_83560d\sensitivity_thresholds.json"

    Write-Host "Starting GuardAId backend..."
    $backendProcess = Start-Process -FilePath $python `
        -WorkingDirectory $monitorRoot `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8020") `
        -RedirectStandardOutput $backendStdout `
        -RedirectStandardError $backendStderr `
        -PassThru
    $script:backendProcess = $backendProcess

    $deadline = (Get-Date).AddSeconds($BackendStartupTimeoutSeconds)
    $healthy = $false
    do {
        Start-Sleep -Milliseconds 500
        if ($backendProcess.HasExited) {
            throw "GuardAId backend exited during startup. Check $backendStderr"
        }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:8020/api/health" -TimeoutSec 3
            $healthy = $true
        } catch {
            # Uvicorn may need a few seconds to import the inference stack.
        }
    } while (!$healthy -and (Get-Date) -lt $deadline)
    if (!$healthy) {
        throw "GuardAId backend did not become healthy within $BackendStartupTimeoutSeconds seconds. Check $backendStderr"
    }
    Write-Host "Backend is healthy at http://127.0.0.1:8020"

    $adbLines = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\S+\s+device\s*$" })
    if ([string]::IsNullOrWhiteSpace($DeviceId)) {
        if ($adbLines.Count -eq 0) {
            throw "No authorized Android device found. Connect and unlock the phone, accept USB debugging, then retry."
        }
        if ($adbLines.Count -gt 1) {
            $ids = ($adbLines | ForEach-Object { ($_ -split "\s+")[0] }) -join ", "
            throw "More than one authorized Android device found ($ids). Re-run with -DeviceId <id>."
        }
        $DeviceId = ($adbLines[0] -split "\s+")[0]
    } elseif (-not ($adbLines | Where-Object { $_ -match "^$([regex]::Escape($DeviceId))\s+device\s*$" })) {
        throw "The requested device '$DeviceId' is not listed as an authorized device by ADB."
    }

    & $adb -s $DeviceId reverse tcp:8020 tcp:8020
    if ($LASTEXITCODE -ne 0) {
        throw "adb reverse failed for device $DeviceId"
    }
    Write-Host "USB tunnel ready for $DeviceId (tcp:8020 -> tcp:8020)"

    Push-Location $mobileRoot
    try {
        $flutterArgs = if ($BuildMode -eq "release") {
            @("build", "apk", "--release")
        } else {
            @("run", "-d", $DeviceId)
        }
        $flutterArgs += @(
            "--dart-define=GUARDAID_LIVE_MODE=true",
            "--dart-define=GUARDAID_BACKEND_URL=http://127.0.0.1:8020",
            "--dart-define=FIREBASE_API_KEY=$firebaseApiKey",
            "--dart-define=FIREBASE_APP_ID=$firebaseAppId",
            "--dart-define=FIREBASE_MESSAGING_SENDER_ID=$firebaseSenderId",
            "--dart-define=FIREBASE_PROJECT_ID=$firebaseProjectId",
            "--dart-define=FIREBASE_STORAGE_BUCKET=$firebaseStorageBucket"
        )
        if ($BuildMode -eq "release") {
            Write-Host "Building GuardAId Live release APK..."
            & $flutter @flutterArgs
            if ($LASTEXITCODE -ne 0) {
                throw "Flutter release build failed."
            }
            $apkPath = Join-Path $mobileRoot "build\app\outputs\flutter-apk\app-release.apk"
            if (!(Test-Path -LiteralPath $apkPath)) {
                throw "Flutter reported success but the release APK was not found at $apkPath."
            }
            & $adb -s $DeviceId install -r $apkPath
            if ($LASTEXITCODE -ne 0) {
                throw "Release APK installation failed for device $DeviceId."
            }
            & $adb -s $DeviceId shell am start -n "com.guardaid.prototype/.MainActivity"
            if ($LASTEXITCODE -ne 0) {
                throw "Could not launch com.guardaid.prototype on device $DeviceId."
            }
            Write-Host "GuardAId Live release is running on $DeviceId. Press Ctrl+C to stop the backend."
            while (!$backendProcess.HasExited) {
                Start-Sleep -Seconds 1
            }
        } else {
            Write-Host "Starting GuardAId Live Mode on $DeviceId. Press q in Flutter to stop the App."
            & $flutter @flutterArgs
        }
    } finally {
        Pop-Location
    }
} finally {
    Stop-Backend
}
