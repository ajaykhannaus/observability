#!/usr/bin/env bash
# =============================================================================
# AI Gateway Telemetry — Azure Infrastructure Bootstrap
# =============================================================================
# Run this ONCE from any machine with az CLI logged in before the first
# GitHub Actions push. It provisions every Azure resource the workflow needs.
#
# Usage:
#   chmod +x infra/bootstrap.sh
#   ./infra/bootstrap.sh \
#     --resource-group  rg-ai-telemetry-prod \
#     --location        eastus \
#     --acr-name        acrtelemetryprod \
#     --cae-name        cae-telemetry-prod \
#     --app-name        ai-telemetry-runner \
#     --eventhub-ns     evhns-telemetry-prod.servicebus.windows.net \
#     --eventhub-conn   "Endpoint=sb://..."
#
# After this script completes it prints the exact GitHub secrets to set.
# =============================================================================
set -euo pipefail

# ── Defaults (override via flags) ─────────────────────────────────────────────
RG="rg-ai-telemetry-prod"
LOCATION="eastus"
ACR_NAME="acrtelemetryprod"
CAE_NAME="cae-telemetry-prod"
APP_NAME="ai-telemetry-runner"
EH_NS=""
EH_CONN=""
EH_NAME="ai-telemetry-events"

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group)  RG="$2";       shift 2 ;;
    --location)        LOCATION="$2"; shift 2 ;;
    --acr-name)        ACR_NAME="$2"; shift 2 ;;
    --cae-name)        CAE_NAME="$2"; shift 2 ;;
    --app-name)        APP_NAME="$2"; shift 2 ;;
    --eventhub-ns)     EH_NS="$2";    shift 2 ;;
    --eventhub-conn)   EH_CONN="$2";  shift 2 ;;
    --eventhub-name)   EH_NAME="$2";  shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo ""
echo "============================================================"
echo "  AI Gateway Telemetry — Azure Bootstrap"
echo "============================================================"
echo "  Resource Group : $RG"
echo "  Location       : $LOCATION"
echo "  ACR Name       : $ACR_NAME"
echo "  CAE Name       : $CAE_NAME"
echo "  App Name       : $APP_NAME"
echo "============================================================"
echo ""

# ── 1. Resource Group ─────────────────────────────────────────────────────────
echo "[1/6] Creating resource group..."
az group create --name "$RG" --location "$LOCATION" --output none
echo "      ✓ $RG"

# ── 2. Azure Container Registry ───────────────────────────────────────────────
echo "[2/6] Creating Azure Container Registry..."
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RG" \
  --sku Basic \
  --admin-enabled true \
  --output none
ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)
echo "      ✓ $ACR_LOGIN_SERVER"

# ── 3. Container Apps CLI extension ──────────────────────────────────────────
echo "[3/6] Ensuring Container Apps CLI extension..."
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true
echo "      ✓ containerapp extension ready"

# ── 4. Container Apps Environment (auto-creates Log Analytics workspace) ──────
echo "[4/6] Creating Container Apps Environment (takes ~2 min)..."
az containerapp env create \
  --name "$CAE_NAME" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --output none
echo "      ✓ $CAE_NAME"

# ── 5. Service Principal for GitHub Actions ───────────────────────────────────
echo "[5/6] Creating Service Principal for GitHub Actions..."
SUB_ID=$(az account show --query id -o tsv)
SP_JSON=$(az ad sp create-for-rbac \
  --name "sp-telemetry-cicd-${RG}" \
  --role Contributor \
  --scopes "/subscriptions/${SUB_ID}/resourceGroups/${RG}" \
  --sdk-auth \
  --output json 2>/dev/null)
# Also grant AcrPush to the SP on the ACR
SP_APP_ID=$(echo "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])")
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role AcrPush \
  --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/Microsoft.ContainerRegistry/registries/${ACR_NAME}" \
  --output none 2>/dev/null || true
echo "      ✓ Service Principal created"

# ── 6. Optional: create Container App now (first image will be pushed by CI) ──
echo "[6/6] Container App will be auto-created by GitHub Actions on first push."
echo "      (bootstrap only creates the environment; deploy.yml handles the app)"

# ── Print GitHub Secrets ──────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Set these secrets in GitHub:"
echo "  Repo → Settings → Secrets and variables → Actions"
echo "============================================================"
echo ""
echo "  AZURE_CREDENTIALS        <<"
echo "$SP_JSON" | sed 's/^/    /'
echo "  >>"
echo ""
echo "  AZURE_RESOURCE_GROUP     $RG"
echo "  ACR_LOGIN_SERVER         $ACR_LOGIN_SERVER"
echo "  ACR_PASSWORD             $ACR_PASSWORD"
echo "  AZURE_CONTAINER_APP_NAME $APP_NAME"
echo "  AZURE_CAE_NAME           $CAE_NAME"
echo ""
echo "Optional (for Prometheus remote-write and Azure Managed Grafana):"
echo "  PROM_REMOTE_WRITE_URL    <Azure Managed Prometheus ingestion URL>"
echo "  AZURE_GRAFANA_NAME       <Azure Managed Grafana resource name>"
echo ""
echo "============================================================"
echo "  Bootstrap complete. Push to main/master to deploy."
echo "============================================================"
echo ""
