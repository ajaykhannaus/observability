# =============================================================================
# AI Gateway Telemetry — Windows Server Self-Hosted Runner Setup
# =============================================================================
# Run this script ONCE on the company Windows Server VM (as Administrator).
# It installs all prerequisites and registers the VM as a GitHub Actions
# self-hosted runner so every push to main/master auto-deploys to Azure.
#
# Usage (run in PowerShell as Administrator):
#   Set-ExecutionPolicy RemoteSigned -Scope Process -Force
#   .\infra\setup-runner.ps1 `
#     -GitHubRepo    "ajaykhanna123ak/azure-telemetry-llm" `
#     -GitHubToken   "<token from GitHub repo → Settings → Actions → Runners → New runner>" `
#     -RunnerName    "company-vm-runner"
# =============================================================================

param(
    [Parameter(Mandatory)]
    [string]$GitHubRepo,        # e.g. "org/azure-telemetry-llm"

    [Parameter(Mandatory)]
    [string]$GitHubToken,       # Registration token from GitHub (expires after 1 hour)

    [string]$RunnerName  = $env:COMPUTERNAME,
    [string]$RunnerDir   = "C:\actions-runner",
    [string]$RunnerLabel = "windows,self-hosted",
    [string]$RunnerVersion = "2.317.0"
)

$ErrorActionPreference = "Stop"

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  AI Gateway Telemetry — Windows Runner Setup" -ForegroundColor Cyan
Write-Host "  Repo   : $GitHubRepo" -ForegroundColor Cyan
Write-Host "  Runner : $RunnerName" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

# ── 1. Install Chocolatey (package manager) ───────────────────────────────────
Write-Host "[1/6] Checking Chocolatey..." -ForegroundColor Yellow
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "      Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:PATH += ";$env:ALLUSERSPROFILE\chocolatey\bin"
}
Write-Host "      ✓ Chocolatey ready" -ForegroundColor Green

# ── 2. Install Azure CLI ──────────────────────────────────────────────────────
Write-Host "[2/6] Checking Azure CLI..." -ForegroundColor Yellow
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "      Installing Azure CLI..."
    choco install azure-cli -y --no-progress
    $env:PATH += ";C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin"
}
Write-Host "      ✓ Azure CLI $(az version --query '\"azure-cli\"' -o tsv)" -ForegroundColor Green

# ── 3. Install Docker ─────────────────────────────────────────────────────────
Write-Host "[3/6] Checking Docker..." -ForegroundColor Yellow
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "      Installing Docker (this may take a few minutes)..."
    # On Windows Server we use Docker Engine directly (no Desktop needed)
    Install-Module -Name DockerMsftProvider -Repository PSGallery -Force -ErrorAction SilentlyContinue
    Install-Package -Name docker -ProviderName DockerMsftProvider -Force -ErrorAction SilentlyContinue

    # Enable Linux containers via Hyper-V / WSL2 if available
    $wsl = Get-WindowsOptionalFeature -FeatureName Microsoft-Windows-Subsystem-Linux -Online -ErrorAction SilentlyContinue
    if ($wsl -and $wsl.State -ne "Enabled") {
        Write-Warning "WSL2 not enabled. Docker may only build Windows containers."
        Write-Warning "For Linux container builds (linux/amd64), enable WSL2 and rerun."
    }
    Start-Service docker
}
docker version | Out-Null
Write-Host "      ✓ Docker $(docker version --format '{{.Server.Version}}')" -ForegroundColor Green

# ── 4. Install Git ────────────────────────────────────────────────────────────
Write-Host "[4/6] Checking Git..." -ForegroundColor Yellow
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "      Installing Git for Windows..."
    choco install git -y --no-progress
    $env:PATH += ";C:\Program Files\Git\cmd"
}
Write-Host "      ✓ Git $(git --version)" -ForegroundColor Green

# ── 5. Install Python 3.11 ───────────────────────────────────────────────────
Write-Host "[5/6] Checking Python..." -ForegroundColor Yellow
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "      Installing Python 3.11..."
    choco install python311 -y --no-progress
    $env:PATH += ";C:\Python311;C:\Python311\Scripts"
}
Write-Host "      ✓ Python $(python --version)" -ForegroundColor Green

# ── 6. Download and register GitHub Actions runner ───────────────────────────
Write-Host "[6/6] Setting up GitHub Actions runner..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null
Push-Location $RunnerDir

$arch    = if ([System.Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$tarball = "actions-runner-win-$arch-$RunnerVersion.zip"
$url     = "https://github.com/actions/runner/releases/download/v$RunnerVersion/$tarball"

if (-not (Test-Path "$RunnerDir\config.cmd")) {
    Write-Host "      Downloading runner v$RunnerVersion..."
    Invoke-WebRequest -Uri $url -OutFile $tarball -UseBasicParsing
    Expand-Archive -Path $tarball -DestinationPath $RunnerDir -Force
    Remove-Item $tarball
}

Write-Host "      Registering runner with GitHub..."
.\config.cmd `
    --url      "https://github.com/$GitHubRepo" `
    --token    $GitHubToken `
    --name     $RunnerName `
    --labels   $RunnerLabel `
    --work     "_work" `
    --unattended `
    --replace

Write-Host "      Installing runner as a Windows Service (auto-starts on reboot)..."
.\svc.cmd install
.\svc.cmd start

Pop-Location

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✓ Runner registered and running as a Windows Service!" -ForegroundColor Green
Write-Host "  Verify at: https://github.com/$GitHubRepo/settings/actions/runners" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next: set these GitHub secrets for your company Azure account:" -ForegroundColor Yellow
Write-Host "  (repo → Settings → Secrets and variables → Actions)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  AZURE_CREDENTIALS        <SP JSON — az ad sp create-for-rbac --sdk-auth>" -ForegroundColor White
Write-Host "  AZURE_RESOURCE_GROUP     <your resource group, e.g. rg-ai-telemetry-dev>" -ForegroundColor White
Write-Host "  ACR_LOGIN_SERVER         <your ACR FQDN, e.g. acrcompanyprod.azurecr.io>" -ForegroundColor White
Write-Host "  AZURE_ACR_NAME           <your ACR short name, e.g. acrcompanyprod>" -ForegroundColor White
Write-Host "  ACR_PASSWORD             <az acr credential show --name acrcompanyprod>" -ForegroundColor White
Write-Host "  AZURE_CONTAINER_APP_NAME ai-telemetry-runner" -ForegroundColor White
Write-Host "  AZURE_CAE_NAME           <your CAE name, e.g. cae-telemetry-prod>" -ForegroundColor White
Write-Host ""
Write-Host "  Then push to main/master — this VM will pick up the job." -ForegroundColor Green
Write-Host ""
