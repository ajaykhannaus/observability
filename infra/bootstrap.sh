#!/usr/bin/env bash
# =============================================================================
# AI Gateway Telemetry — Azure Infrastructure Bootstrap (production)
# =============================================================================
# Run this ONCE from any machine with the Azure CLI logged in (`az login`).
# It provisions every Azure resource the deploy workflow needs and prints
# the exact GitHub secrets to copy.
#
# What it creates / verifies (idempotent — re-running is safe):
#   1. Resource group
#   2. Azure Container Registry (Standard SKU, admin DISABLED)
#   3. Container Apps environment (auto-creates a Log Analytics workspace)
#   4. Event Hubs namespace + event hub (skip with --skip-eventhub)
#   5. Service Principal for GitHub Actions (least-privilege roles only)
#
# Usage:
#   chmod +x infra/bootstrap.sh
#   ./infra/bootstrap.sh \
#     --resource-group rg-ai-telemetry-prod \
#     --location       eastus \
#     --acr-name       acrtelemetryprod \
#     --cae-name       cae-telemetry-prod \
#     --app-name       ai-telemetry-runner \
#     --eventhub-ns    evhns-telemetry-prod \
#     --eventhub-name  ai-telemetry-events
#
# Add --preflight to verify Azure access and quota WITHOUT creating anything.
# Add --skip-eventhub if you already have an Event Hubs namespace.
#
# Naming rules enforced by Azure:
#   * ACR name      : 5-50 chars, alphanumeric only, globally unique
#   * EH namespace  : 6-50 chars, start with letter, alnum + hyphen, globally unique
# =============================================================================
set -euo pipefail

# ── Defaults (override via flags) ─────────────────────────────────────────────
RG="rg-ai-telemetry-prod"
LOCATION="eastus"
ACR_NAME="acrtelemetryprod"
CAE_NAME="cae-telemetry-prod"
APP_NAME="ai-telemetry-runner"
EH_NS=""
EH_NAME="ai-telemetry-events"
SKIP_EVENTHUB=false
PREFLIGHT=false

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group)  RG="$2";       shift 2 ;;
    --location)        LOCATION="$2"; shift 2 ;;
    --acr-name)        ACR_NAME="$2"; shift 2 ;;
    --cae-name)        CAE_NAME="$2"; shift 2 ;;
    --app-name)        APP_NAME="$2"; shift 2 ;;
    --eventhub-ns)     EH_NS="$2";    shift 2 ;;
    --eventhub-name)   EH_NAME="$2";  shift 2 ;;
    --skip-eventhub)   SKIP_EVENTHUB=true; shift ;;
    --preflight)       PREFLIGHT=true;     shift ;;
    -h|--help)
      sed -n '2,40p' "$0"
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: Azure CLI ('az') is not installed." >&2
  echo "Install: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "ERROR: Not logged in to Azure. Run: az login" >&2
  exit 1
fi

SUB_ID=$(az account show --query id -o tsv)
SUB_NAME=$(az account show --query name -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# Sensible default for EH namespace if the caller didn't pass one.
if [[ -z "$EH_NS" && "$SKIP_EVENTHUB" == "false" ]]; then
  EH_NS="evhns-${APP_NAME}-$(echo "$SUB_ID" | cut -c1-6)"
fi

echo ""
echo "============================================================"
echo "  AI Gateway Telemetry — Azure Bootstrap"
echo "============================================================"
echo "  Subscription   : $SUB_NAME ($SUB_ID)"
echo "  Tenant         : $TENANT_ID"
echo "  Resource Group : $RG"
echo "  Location       : $LOCATION"
echo "  ACR Name       : $ACR_NAME"
echo "  CAE Name       : $CAE_NAME"
echo "  App Name       : $APP_NAME"
if [[ "$SKIP_EVENTHUB" == "false" ]]; then
  echo "  EH Namespace   : $EH_NS"
  echo "  EH Name        : $EH_NAME"
else
  echo "  Event Hubs     : --skip-eventhub (bring your own connection string)"
fi
echo "  Mode           : $([[ "$PREFLIGHT" == "true" ]] && echo "PREFLIGHT (no changes)" || echo "create / update")"
echo "============================================================"
echo ""

# ── Preflight checks ──────────────────────────────────────────────────────────
echo "[preflight] Validating Azure access..."

# Provider registrations
required_providers=(
  Microsoft.ContainerRegistry
  Microsoft.OperationalInsights
  Microsoft.App
  Microsoft.EventHub
)
for ns in "${required_providers[@]}"; do
  state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$state" != "Registered" ]]; then
    if [[ "$PREFLIGHT" == "true" ]]; then
      echo "  ! $ns is $state (would be registered)"
    else
      echo "  → Registering $ns ..."
      az provider register --namespace "$ns" --output none
    fi
  else
    echo "  ✓ $ns registered"
  fi
done

# ACR name availability
ACR_AVAIL=$(az acr check-name --name "$ACR_NAME" --query nameAvailable -o tsv 2>/dev/null || echo "false")
if [[ "$ACR_AVAIL" != "true" ]]; then
  # Already-existing ACR is fine ONLY if it's already in our target RG/sub.
  if az acr show --name "$ACR_NAME" --resource-group "$RG" >/dev/null 2>&1; then
    echo "  ✓ ACR $ACR_NAME already exists in $RG"
  else
    echo "  ! ACR name '$ACR_NAME' is taken globally and not in $RG — pick another with --acr-name" >&2
    [[ "$PREFLIGHT" == "true" ]] || exit 1
  fi
else
  echo "  ✓ ACR name '$ACR_NAME' available"
fi

if [[ "$PREFLIGHT" == "true" ]]; then
  echo ""
  echo "Preflight complete. Re-run without --preflight to create resources."
  exit 0
fi

# ── 1. Resource Group ─────────────────────────────────────────────────────────
echo ""
echo "[1/5] Creating resource group..."
az group create --name "$RG" --location "$LOCATION" --output none
echo "      ✓ $RG"

# ── 2. Azure Container Registry (admin disabled — uses SP / managed identity) ─
echo "[2/5] Creating Azure Container Registry (admin disabled)..."
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RG" \
  --sku Standard \
  --admin-enabled false \
  --output none
ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RG" --query loginServer -o tsv)
echo "      ✓ $ACR_LOGIN_SERVER"

# ── 3. Container Apps environment + extension ─────────────────────────────────
echo "[3/5] Creating Container Apps environment (takes ~2 min)..."
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true
az containerapp env create \
  --name "$CAE_NAME" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --output none
echo "      ✓ $CAE_NAME"

# ── 4. Event Hubs namespace + event hub ───────────────────────────────────────
EH_CONN=""
if [[ "$SKIP_EVENTHUB" == "false" ]]; then
  echo "[4/5] Creating Event Hubs namespace + event hub..."
  az eventhubs namespace create \
    --name "$EH_NS" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    --sku Standard \
    --capacity 1 \
    --output none
  az eventhubs eventhub create \
    --name "$EH_NAME" \
    --namespace-name "$EH_NS" \
    --resource-group "$RG" \
    --partition-count 4 \
    --retention-time 24 \
    --output none
  EH_CONN=$(az eventhubs namespace authorization-rule keys list \
    --resource-group "$RG" \
    --namespace-name "$EH_NS" \
    --name RootManageSharedAccessKey \
    --query primaryConnectionString -o tsv)
  echo "      ✓ $EH_NS / $EH_NAME"
else
  echo "[4/5] Skipping Event Hubs (--skip-eventhub set)"
fi

# ── 5. Service Principal for GitHub Actions (least privilege) ─────────────────
echo "[5/5] Creating Service Principal for GitHub Actions..."
SP_NAME="sp-telemetry-cicd-${RG}"

# Re-use existing SP if present, else create a fresh one.
EXISTING_APP_ID=$(az ad sp list --display-name "$SP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
if [[ -n "$EXISTING_APP_ID" ]]; then
  echo "      (SP $SP_NAME already exists — resetting credentials)"
  SP_JSON=$(az ad sp credential reset \
    --id "$EXISTING_APP_ID" \
    --display-name "$(date -u +%Y%m%d-%H%M%S)" \
    --query '{clientId:appId,clientSecret:password,tenantId:tenant,subscriptionId:`'$SUB_ID'`}' \
    --output json)
  # Convert the reset output to the --sdk-auth-compatible shape used by azure/login@v2
  SP_JSON=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print(json.dumps({
  'clientId':       d['clientId'],
  'clientSecret':   d['clientSecret'],
  'subscriptionId': '$SUB_ID',
  'tenantId':       '$TENANT_ID',
  'activeDirectoryEndpointUrl':       'https://login.microsoftonline.com',
  'resourceManagerEndpointUrl':       'https://management.azure.com/',
  'activeDirectoryGraphResourceId':   'https://graph.windows.net/',
  'sqlManagementEndpointUrl':         'https://management.core.windows.net:8443/',
  'galleryEndpointUrl':               'https://gallery.azure.com/',
  'managementEndpointUrl':            'https://management.core.windows.net/',
}, indent=2))" "$SP_JSON")
else
  SP_JSON=$(az ad sp create-for-rbac \
    --name "$SP_NAME" \
    --role Reader \
    --scopes "/subscriptions/${SUB_ID}/resourceGroups/${RG}" \
    --sdk-auth \
    --output json)
fi

SP_APP_ID=$(python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])" <<<"$SP_JSON")

# AcrPush on the registry (image push)
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role AcrPush \
  --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/Microsoft.ContainerRegistry/registries/${ACR_NAME}" \
  --output none 2>/dev/null || true

# Container Apps Contributor on the RG (create/update Container Apps)
az role assignment create \
  --assignee "$SP_APP_ID" \
  --role "Container Apps Contributor" \
  --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}" \
  --output none 2>/dev/null || true

echo "      ✓ Service Principal $SP_NAME with least-privilege roles"

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
echo "  AZURE_SUBSCRIPTION_ID      $SUB_ID"
echo "  AZURE_TENANT_ID            $TENANT_ID"
echo "  AZURE_RESOURCE_GROUP       $RG"
echo "  AZURE_LOCATION             $LOCATION"
echo "  ACR_LOGIN_SERVER           $ACR_LOGIN_SERVER"
echo "  AZURE_ACR_NAME             $ACR_NAME"
echo "  AZURE_CONTAINER_APP_NAME   $APP_NAME"
echo "  AZURE_CAE_NAME             $CAE_NAME"
if [[ -n "$EH_CONN" ]]; then
  echo "  EVENTHUB_NAMESPACE         ${EH_NS}.servicebus.windows.net"
  echo "  EVENTHUB_CONNECTION_STRING $EH_CONN"
fi
echo ""
echo "  (ACR admin is DISABLED — image push uses the Service Principal."
echo "   No ACR_PASSWORD secret needed.)"
echo ""
echo "Optional (for Prometheus remote-write and Azure Managed Grafana):"
echo "  PROM_REMOTE_WRITE_URL    <Azure Managed Prometheus ingestion URL>"
echo "  AZURE_GRAFANA_NAME       <Azure Managed Grafana resource name>"
echo ""
echo "============================================================"
echo "  Bootstrap complete. Push to main/master to deploy."
echo "============================================================"
echo ""
