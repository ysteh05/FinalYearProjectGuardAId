param(
    [string]$VenvPath = "C:\tmp\guardaid-vlm-venv"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Create a CUDA-enabled Python environment first, or pass its location using -VenvPath."
}
Write-Host "Checking the existing CUDA runtime..." -ForegroundColor Cyan
& $Python -c "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('PyTorch', torch.__version__, '| CUDA', torch.version.cuda, '|', torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "The environment needs a working CUDA-enabled PyTorch installation." }
Write-Host "Installing GuardAId backend dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
Set-Location -LiteralPath $ProjectRoot
Write-Host "Checking the local Ollama MiniCPM model..." -ForegroundColor Cyan
& $Python -c "import av, cv2, firebase_admin; from app.inference.minicpm import MiniCpmOllamaRunner; r=MiniCpmOllamaRunner(); r.load(); print(r.MODEL_VERSION, 'ready through local Ollama.')"
if ($LASTEXITCODE -ne 0) { throw "Start Ollama and install openbmb/minicpm-v4.7:1b-q4_K_M, then retry." }
Write-Host "Setup complete. Start GuardAId with .\run_demo.ps1" -ForegroundColor Green
