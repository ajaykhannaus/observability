# ============================================================================
# AI Gateway Telemetry — Windows Developer Quickstart
# ============================================================================
# Run this on your Windows machine to set up the dev environment.
# Uses: winget, Docker Desktop, Python 3.11, Azure CLI
# ============================================================================

Write-Host "== AI Gateway Telemetry — Windows Dev Setup ==" -ForegroundColor Cyan

# ── Step 1: Install prerequisites (run once, as Administrator) ────────────────
function Install-Prerequisites {
    Write-Host "`n[Step 1] Installing prerequisites..." -ForegroundColor Yellow

    # Azure CLI
    winget install --id Microsoft.AzureCLI -e --accept-source-agreements --accept-package-agreements

    # Docker Desktop (requires restart)
    winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements

    # Python 3.11
    winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements

    # Git
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements

    Write-Host "  Prerequisites installed. Restart PowerShell before continuing." -ForegroundColor Green
    Write-Host "  Then enable Docker buildx: docker buildx install" -ForegroundColor Gray
}

# ── Step 2: Clone repo and set up Python env ─────────────────────────────────
function Setup-PythonEnv {
    Write-Host "`n[Step 2] Setting up Python environment..." -ForegroundColor Yellow

    # Note: on Windows use 'python' not 'python3'
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1

    python -m pip install --upgrade pip
    pip install -r generator\requirements.txt
    pip install opentelemetry-exporter-prometheus

    Write-Host "  Python env ready. Activate with: .\.venv\Scripts\Activate.ps1" -ForegroundColor Green
}

# ── Step 3: Create .env file from template ────────────────────────────────────
function Setup-EnvFile {
    Write-Host "`n[Step 3] Creating .env file..." -ForegroundColor Yellow

    if (Test-Path ".env") {
        Write-Warning ".env already exists — skipping. Edit it manually."
        return
    }

    $envContent = @"
# Azure credentials (get from your company Azure admin)
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-sp-client-id
AZURE_CLIENT_SECRET=your-sp-secret
AZURE_SUBSCRIPTION_ID=your-subscription-id

# Event Hub
EVENTHUB_NAMESPACE=evhns-telemetry.servicebus.windows.net
EVENTHUB_NAME=ai-telemetry-events
EVENTHUB_CONNECTION_STRING=Endpoint=sb://evhns-telemetry.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=YOUR_KEY

# App config
OTEL_SERVICE_NAME=ai-telemetry-poc
ENVIRONMENT=dev
PROMETHEUS_PORT=8000
BATCH_INTERVAL_S=5
BASE_BATCH_SIZE=8
"@
    $envContent | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "  .env created. Fill in your Azure values before running." -ForegroundColor Green
    Write-Host "  NEVER commit .env to git." -ForegroundColor Red
}

# ── Step 4: Run generator locally (mock mode — no Azure needed) ───────────────
function Run-MockMode {
    Write-Host "`n[Step 4] Starting generator in mock mode..." -ForegroundColor Yellow
    Write-Host "  No Azure connection needed. Events logged locally only." -ForegroundColor Gray

    $env:PROMETHEUS_PORT = "8000"
    $env:ENVIRONMENT = "local"

    python -m generator.runner
}

# ── Step 5: Login to Azure and push image ─────────────────────────────────────
function Push-Image {
    param([string]$AcrName, [string]$ImageTag = "latest")

    Write-Host "`n[Step 5] Building and pushing Docker image..." -ForegroundColor Yellow

    # Login
    az login --output none
    az acr login --name $AcrName

    # Build for linux/amd64 (required for Azure — Windows Docker Desktop uses buildx)
    docker buildx build `
        --platform linux/amd64 `
        -f Dockerfile.runner `
        -t "${AcrName}.azurecr.io/ai-telemetry-runner:${ImageTag}" `
        --push .

    Write-Host "  Image pushed: ${AcrName}.azurecr.io/ai-telemetry-runner:${ImageTag}" -ForegroundColor Green
}

# ── Step 6: Open Azure Managed Grafana ────────────────────────────────────────
function Open-Grafana {
    param([string]$GrafanaName, [string]$ResourceGroup)

    $url = az grafana show `
        --name $GrafanaName `
        --resource-group $ResourceGroup `
        --query "properties.endpoint" -o tsv

    Write-Host "`nOpening Grafana: $url" -ForegroundColor Cyan
    Start-Process $url
}

# ── Step 7: Tail live Container App logs ──────────────────────────────────────
function Watch-Logs {
    param(
        [string]$AppName     = "ai-telemetry-runner",
        [string]$ResourceGroup
    )

    Write-Host "`n[Step 7] Streaming Container App logs..." -ForegroundColor Yellow
    Write-Host "  Press Ctrl+C to stop." -ForegroundColor Gray

    az containerapp logs show `
        --name $AppName `
        --resource-group $ResourceGroup `
        --follow `
        --format json | ForEach-Object {
            $log = $_ | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($log) {
                $level = $log.level
                $color = if ($level -eq "ERROR") { "Red" } elseif ($level -eq "WARNING") { "Yellow" } else { "White" }
                Write-Host "[$($log.timestamp)] [$level] $($log.message)" -ForegroundColor $color
            }
        }
}

# ── Main menu ─────────────────────────────────────────────────────────────────
Write-Host @"

Available functions (run in PowerShell):

  Install-Prerequisites    # Install az, docker, python, git via winget
  Setup-PythonEnv          # Create venv + install packages
  Setup-EnvFile            # Create .env template
  Run-MockMode             # Run generator locally (no Azure)
  Push-Image -AcrName 'acrtelemetrycorp'
  Open-Grafana -GrafanaName 'grafana-ai-telemetry' -ResourceGroup 'rg-ai-telemetry-dev'
  Watch-Logs -AppName 'ai-telemetry-runner' -ResourceGroup 'rg-ai-telemetry-dev'

"@ -ForegroundColor Gray
