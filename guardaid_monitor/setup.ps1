param(
    [string]$VenvPath = "C:\tmp\guardaid-vlm-venv"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "The shared GPU environment was not found at $VenvPath. Run vlm_borderline_dashboard\setup.ps1 first."
}

Write-Host "Checking the existing CUDA runtime..." -ForegroundColor Cyan
& $Python -c "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('PyTorch', torch.__version__, '| CUDA', torch.version.cuda, '|', torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) {
    throw "The existing environment does not have a working CUDA-enabled PyTorch installation."
}

Write-Host "Installing GuardAId backend and Qwen dependencies into the shared environment..." -ForegroundColor Cyan
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

$PackagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
$FFmpegDll = Get-ChildItem -LiteralPath $PackagesRoot -Directory -Filter "Gyan.FFmpeg.Shared*" -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -Filter "avcodec-*.dll" -ErrorAction SilentlyContinue } |
    Select-Object -First 1

if (-not $FFmpegDll) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "TorchCodec needs shared FFmpeg. Install Gyan.FFmpeg.Shared, then run this script again."
    }
    Write-Host "Installing the shared FFmpeg DLLs required by TorchCodec..." -ForegroundColor Cyan
    winget install --id Gyan.FFmpeg.Shared -e --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Shared FFmpeg installation failed." }
    $FFmpegDll = Get-ChildItem -LiteralPath $PackagesRoot -Directory -Filter "Gyan.FFmpeg.Shared*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Recurse -Filter "avcodec-*.dll" -ErrorAction SilentlyContinue } |
        Select-Object -First 1
}

if (-not $FFmpegDll) { throw "Shared FFmpeg was installed but its DLL folder could not be found." }
$env:PATH = "$($FFmpegDll.DirectoryName);$env:PATH"

Write-Host "Validating all Qwen3-VL runtime imports..." -ForegroundColor Cyan
& $Python -c "import torch, transformers, accelerate, bitsandbytes, torchcodec, firebase_admin; from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig; print('Qwen3-VL runtime is ready.')"
if ($LASTEXITCODE -ne 0) { throw "Qwen3-VL runtime validation failed; review the error printed above." }

Write-Host "Setup complete. Start GuardAId with .\run_demo.ps1" -ForegroundColor Green
