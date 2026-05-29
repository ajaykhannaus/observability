#!/usr/bin/env bash
# =============================================================================
# Azure Cloud Shell — one-time dev environment setup
# =============================================================================
# Run in Bash (Azure Cloud Shell or VS Code Web on Azure):
#
#   git clone https://github.com/ajaykhannaus/observability.git
#   cd observability
#   chmod +x scripts/azure-cloudshell-setup.sh
#   ./scripts/azure-cloudshell-setup.sh
#
# Optional: set subscription before running
#   export AZURE_SUBSCRIPTION_ID="<your-subscription-guid>"
#
# Use an existing resource group (no permission to create RG):
#   export AZURE_RESOURCE_GROUP="<your-company-rg-name>"
#   export USE_EXISTING_RG=true
#   ./scripts/azure-cloudshell-setup.sh
#
# Cloud Shell is already logged in — no az login or .env required for this script.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Dev resource names ────────────────────────────────────────────────────────
RG="${AZURE_RESOURCE_GROUP:-rg-ai-telemetry-dev}"
LOCATION="${AZURE_LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-acrtelemetrydev}"
CAE_NAME="${CAE_NAME:-cae-telemetry-dev}"
APP_NAME="${APP_NAME:-ai-telemetry-runner-dev}"
EH_NS="${EH_NS:-evhns-telemetry-dev}"
EH_NAME="${EVENTHUB_NAME:-ai-telemetry-events}"
USE_EXISTING_RG="${USE_EXISTING_RG:-false}"

for arg in "$@"; do
  case "$arg" in
    --use-existing-rg) USE_EXISTING_RG=true ;;
  esac
done

log() { echo "[cloudshell-setup] $*"; }

# ── 1. Subscription ───────────────────────────────────────────────────────────
log "Current Azure account:"
az account show --query "{subscription:name, id:id, tenant:tenantId, user:user.name}" -o table

if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  log "Setting subscription to $AZURE_SUBSCRIPTION_ID ..."
  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
fi

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# ── 2. Register providers ─────────────────────────────────────────────────────
log "Registering Azure resource providers (one-time per subscription)..."
for ns in \
  Microsoft.App \
  Microsoft.ContainerRegistry \
  Microsoft.EventHub \
  Microsoft.Monitor \
  Microsoft.Dashboard \
  Microsoft.OperationalInsights; do
  state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$state" == "Registered" ]]; then
    log "  ✓ $ns"
  else
    log "  → Registering $ns ..."
    # Requires Contributor/Owner at subscription scope; warn and continue if lacking.
    az provider register --namespace "$ns" --output none 2>/dev/null || \
      log "  WARNING: could not register $ns (insufficient subscription-level permissions — ask a subscription Owner)."
  fi
done

log "Waiting for providers to finish registering..."
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.EventHub \
          Microsoft.Monitor Microsoft.Dashboard Microsoft.OperationalInsights; do
  az provider register --namespace "$ns" --wait --output none 2>/dev/null || true
done

# ── 3. Resource group ─────────────────────────────────────────────────────────
if [[ "$USE_EXISTING_RG" == "true" ]]; then
  log "Using existing resource group $RG ..."
  if ! az group show --name "$RG" >/dev/null 2>&1; then
    echo "ERROR: Resource group '$RG' not found." >&2
    echo "       export AZURE_RESOURCE_GROUP='<name-from-your-admin>'" >&2
    exit 1
  fi
  LOCATION=$(az group show --name "$RG" --query location -o tsv)
  log "  ✓ Found $RG (location: $LOCATION)"
else
  log "Creating resource group $RG in $LOCATION ..."
  if az group show --name "$RG" >/dev/null 2>&1; then
    log "  ✓ Resource group $RG already exists"
  else
    az group create \
      --name "$RG" \
      --location "$LOCATION" \
      --tags project=observability environment=dev \
      --output none
    log "  ✓ Created $RG"
  fi
fi

az group show --name "$RG" \
  --query "{name:name, location:location, state:properties.provisioningState}" -o table

BOOTSTRAP_ARGS=(
  --resource-group "$RG"
  --location       "$LOCATION"
  --acr-name       "$ACR_NAME"
  --cae-name       "$CAE_NAME"
  --app-name       "$APP_NAME"
  --eventhub-ns    "$EH_NS"
  --eventhub-name  "$EH_NAME"
)
if [[ "$USE_EXISTING_RG" == "true" ]]; then
  BOOTSTRAP_ARGS+=(--use-existing-rg)
fi

# ── 4. Bootstrap (ACR, Container Apps env, Event Hubs, CI/CD SP) ────────────
log "Running infra/bootstrap.sh ..."
chmod +x "$ROOT/infra/bootstrap.sh"
"$ROOT/infra/bootstrap.sh" "${BOOTSTRAP_ARGS[@]}"

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Cloud Shell setup complete"
echo "============================================================"
echo "  Resource group     : $RG"
echo "  Location           : $LOCATION"
echo "  ACR                : $ACR_NAME"
echo "  Container Apps env : $CAE_NAME"
echo "  Event Hub namespace: $EH_NS"
echo "  Subscription       : $SUB_ID"
echo "  Tenant             : $TENANT_ID"
echo ""
echo "  Next steps (still in Cloud Shell):"
echo "    az acr build --registry $ACR_NAME --image ai-telemetry-runner:latest -f Dockerfile.runner ."
echo "    az acr build --registry $ACR_NAME --image prometheus-scraper:latest -f Dockerfile.prometheus ."
echo ""
echo "  Then deploy from your Mac (secrets in local .env only):"
echo "    ./scripts/deploy-local.sh deploy"
echo "    ./scripts/deploy-local.sh grafana"
echo "============================================================"
