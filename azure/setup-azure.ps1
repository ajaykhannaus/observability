# AI Gateway Telemetry — Azure Setup (Windows PowerShell)
# Usage: .\azure\setup-azure.ps1 -ResourceGroup "rg-telemetry-dev" -Location "eastus"

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroup,

    [string]$Location          = "eastus",
    [string]$AppName           = "ai-telemetry-runner",
    [string]$PrometheusAppName = "prometheus-scraper",
    [string]$AcrName           = "acrtelemetrycorp",
    [string]$GrafanaName       = "grafana-ai-telemetry",
    [string]$PromWorkspaceName = "telemetry-prometheus-ws",
    [string]$CaeName           = "cae-telemetry",
    [string]$EventHubNs        = "evhns-telemetry",
    [string]$EventHubName      = "ai-telemetry-events"
)

$ErrorActionPreference = "Stop"

Write-Host "`n== AI Gateway Telemetry — Company Azure Setup ==" -ForegroundColor Cyan
Write-Host "Resource Group : $ResourceGroup" -ForegroundColor Gray
Write-Host "Location       : $Location" -ForegroundColor Gray

# ── 0. Login & register providers ────────────────────────────────────────────
Write-Host "`n[1/9] Logging in to Azure..." -ForegroundColor Yellow
az login --output none

Write-Host "[1/9] Registering required providers (Microsoft.App, Microsoft.Monitor, Microsoft.Dashboard)..."
az provider register -n Microsoft.App            --wait
az provider register -n Microsoft.Monitor        --wait
az provider register -n Microsoft.Dashboard      --wait
az provider register -n Microsoft.OperationalInsights --wait
Write-Host "      Providers registered." -ForegroundColor Green

# ── 1. Resource group ─────────────────────────────────────────────────────────
Write-Host "`n[2/9] Creating resource group..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location --output none
Write-Host "      Done." -ForegroundColor Green

# ── 2. Azure Container Registry ──────────────────────────────────────────────
Write-Host "`n[3/9] Creating Azure Container Registry ($AcrName)..." -ForegroundColor Yellow
az acr create `
    --name $AcrName `
    --resource-group $ResourceGroup `
    --sku Basic `
    --admin-enabled false `
    --output none

# Grant current user AcrPush so they can push images from this machine
$CurrentUser = az ad signed-in-user show --query id -o tsv
az role assignment create `
    --assignee $CurrentUser `
    --role AcrPush `
    --scope (az acr show --name $AcrName --resource-group $ResourceGroup --query id -o tsv) `
    --output none
Write-Host "      ACR ready. AcrPush granted to current user." -ForegroundColor Green

# ── 3. Azure Managed Prometheus workspace ────────────────────────────────────
Write-Host "`n[4/9] Creating Azure Managed Prometheus workspace..." -ForegroundColor Yellow
az monitor account create `
    --name $PromWorkspaceName `
    --resource-group $ResourceGroup `
    --location $Location `
    --output none

$PromQueryEndpoint = az monitor account show `
    --name $PromWorkspaceName `
    --resource-group $ResourceGroup `
    --query "metrics.prometheusQueryEndpoint" -o tsv

$PromIngestEndpoint = az monitor account show `
    --name $PromWorkspaceName `
    --resource-group $ResourceGroup `
    --query "defaultIngestionSettings.dataCollectionEndpointResourceId" -o tsv

Write-Host "      Query endpoint : $PromQueryEndpoint" -ForegroundColor Gray
Write-Host "      Prometheus workspace ready." -ForegroundColor Green

# ── 4. Azure Managed Grafana ──────────────────────────────────────────────────
Write-Host "`n[5/9] Creating Azure Managed Grafana (this takes ~3 min)..." -ForegroundColor Yellow
az grafana create `
    --name $GrafanaName `
    --resource-group $ResourceGroup `
    --location $Location `
    --sku Standard `
    --output none

$GrafanaEndpoint = az grafana show `
    --name $GrafanaName `
    --resource-group $ResourceGroup `
    --query "properties.endpoint" -o tsv

Write-Host "      Grafana URL : $GrafanaEndpoint" -ForegroundColor Gray

# Link Azure Managed Prometheus to Managed Grafana (native integration — no tokens needed)
$PromWorkspaceId = az monitor account show `
    --name $PromWorkspaceName `
    --resource-group $ResourceGroup `
    --query id -o tsv

az grafana integrations add `
    --name $GrafanaName `
    --resource-group $ResourceGroup `
    --workspace-id $PromWorkspaceId `
    --output none

Write-Host "      Managed Prometheus linked to Managed Grafana (no token management needed)." -ForegroundColor Green

# ── 5. Container Apps Environment ────────────────────────────────────────────
Write-Host "`n[6/9] Creating Container Apps Environment..." -ForegroundColor Yellow
az containerapp env create `
    --name $CaeName `
    --resource-group $ResourceGroup `
    --location $Location `
    --output none
Write-Host "      Environment ready." -ForegroundColor Green

# ── 6. Event Hub Namespace & Hub ──────────────────────────────────────────────
Write-Host "`n[7/9] Creating Event Hub..." -ForegroundColor Yellow
az eventhubs namespace create `
    --name $EventHubNs `
    --resource-group $ResourceGroup `
    --location $Location `
    --sku Standard `
    --output none

az eventhubs eventhub create `
    --name $EventHubName `
    --namespace-name $EventHubNs `
    --resource-group $ResourceGroup `
    --partition-count 4 `
    --output none

$EventHubConn = az eventhubs namespace authorization-rule keys list `
    --resource-group $ResourceGroup `
    --namespace-name $EventHubNs `
    --name RootManageSharedAccessKey `
    --query primaryConnectionString -o tsv

Write-Host "      Event Hub ready." -ForegroundColor Green

# ── 7. Get DCR immutable ID for Prometheus remote_write ──────────────────────
Write-Host "`n[8/9] Retrieving Prometheus ingest DCR details..." -ForegroundColor Yellow

# The managed RG is auto-created by Azure Monitor
$ManagedRg = "MA_${PromWorkspaceName}_${Location}_managed"
$DcrImmutableId = ""
$DceEndpoint = ""

# Poll for managed RG (can take up to 60s)
$retries = 0
while ($DcrImmutableId -eq "" -and $retries -lt 12) {
    Start-Sleep -Seconds 10
    $retries++
    try {
        $DcrImmutableId = az monitor data-collection rule list `
            --resource-group $ManagedRg `
            --query "[0].immutableId" -o tsv 2>$null
        $DceEndpoint = az monitor data-collection endpoint list `
            --resource-group $ManagedRg `
            --query "[0].properties.logsIngestion.endpoint" -o tsv 2>$null
    } catch { }
}

if ($DcrImmutableId -eq "") {
    Write-Warning "Could not retrieve DCR automatically. Check Azure Portal for Managed Prometheus DCR details."
} else {
    Write-Host "      DCR immutable ID : $DcrImmutableId" -ForegroundColor Gray
    Write-Host "      DCE endpoint     : $DceEndpoint" -ForegroundColor Gray
}

# ── 8. Deploy Container Apps ──────────────────────────────────────────────────
Write-Host "`n[9/9] Deploying Container Apps (runner + prometheus-scraper)..." -ForegroundColor Yellow

# 8a. Runner Container App (skip create if same name already exists)
if (az containerapp show --name $AppName --resource-group $ResourceGroup 2>$null) {
    Write-Host "      Runner $AppName already exists — skipping create" -ForegroundColor Gray
} else {
    az containerapp create `
        --name $AppName `
        --resource-group $ResourceGroup `
        --environment $CaeName `
        --image "${AcrName}.azurecr.io/ai-telemetry-runner:latest" `
        --registry-server "${AcrName}.azurecr.io" `
        --ingress external --target-port 8000 `
        --min-replicas 1 --max-replicas 1 `
        --cpu 0.5 --memory 1Gi `
        --system-assigned `
        --env-vars `
            OTEL_SERVICE_NAME=ai-telemetry-poc `
            ENVIRONMENT=production `
            EVENTHUB_NAMESPACE="${EventHubNs}.servicebus.windows.net" `
            EVENTHUB_NAME=$EventHubName `
            PROMETHEUS_PORT=8000 `
            BATCH_INTERVAL_S=5 `
            BASE_BATCH_SIZE=8 `
            EVENTHUB_CONNECTION_STRING="secretref:eventhub-conn-str" `
        --secrets "eventhub-conn-str=$EventHubConn" `
        --output none
}

$RunnerFqdn = az containerapp show `
    --name $AppName `
    --resource-group $ResourceGroup `
    --query "properties.configuration.ingress.fqdn" -o tsv

# Grant runner managed identity AcrPull
$RunnerPrincipalId = az containerapp show `
    --name $AppName --resource-group $ResourceGroup `
    --query "identity.principalId" -o tsv
$AcrId = az acr show --name $AcrName --resource-group $ResourceGroup --query id -o tsv
az role assignment create --assignee $RunnerPrincipalId --role AcrPull --scope $AcrId --output none

Write-Host "      Runner FQDN : $RunnerFqdn" -ForegroundColor Gray

# 8b. Prometheus scraper Container App (skip create if same name already exists)
if (az containerapp show --name $PrometheusAppName --resource-group $ResourceGroup 2>$null) {
    Write-Host "      Prometheus $PrometheusAppName already exists — skipping create" -ForegroundColor Gray
} else {
    az containerapp create `
        --name $PrometheusAppName `
        --resource-group $ResourceGroup `
        --environment $CaeName `
        --image "prom/prometheus:latest" `
        --ingress internal --target-port 9090 `
        --min-replicas 1 --max-replicas 1 `
        --cpu 0.25 --memory 0.5Gi `
        --args "--config.file=/etc/prometheus/prometheus.yml" `
               "--storage.tsdb.retention.time=2h" `
               "--log.level=warn" `
        --output none
}

Write-Host "      Prometheus scraper deployed." -ForegroundColor Green

# ── 9. Import dashboard to Managed Grafana ────────────────────────────────────
Write-Host "`nImporting Grafana dashboard..." -ForegroundColor Yellow
$DashboardJson = Get-Content "dashboards\grafana_dashboard.json" -Raw
az grafana dashboard import `
    --name $GrafanaName `
    --resource-group $ResourceGroup `
    --definition $DashboardJson `
    --overwrite true `
    --output none
Write-Host "      Dashboard imported." -ForegroundColor Green

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host " SETUP COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Grafana URL    : $GrafanaEndpoint" -ForegroundColor White
Write-Host " Runner FQDN   : https://$RunnerFqdn/metrics" -ForegroundColor White
Write-Host " Prometheus     : Internal (prometheus-scraper container app)" -ForegroundColor White
Write-Host " Event Hub      : $EventHubNs.servicebus.windows.net" -ForegroundColor White
Write-Host "`n Next steps:" -ForegroundColor Yellow
Write-Host "  1. Open Grafana: $GrafanaEndpoint (login with Azure AD)" -ForegroundColor Gray
Write-Host "  2. Verify Azure Managed Prometheus datasource is auto-configured" -ForegroundColor Gray
Write-Host "  3. Add GitHub secret: AZURE_CONTAINER_APP_NAME = $AppName" -ForegroundColor Gray
Write-Host "  4. Push to master to trigger CI/CD deploy" -ForegroundColor Gray
Write-Host "============================================================`n" -ForegroundColor Cyan
