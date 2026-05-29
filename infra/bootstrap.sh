#!/usr/bin/env bash
set -euo pipefail

RG="rg-ai-telemetry-dev"
LOCATION="eastus"
ACR_NAME="acrtelemetrydev"
CAE_NAME="cae-telemetry-dev"
APP_NAME="ai-telemetry-runner-dev"
EH_NS="evhns-telemetry-dev"
EH_NAME="ai-telemetry-events"
SKIP_EVENTHUB=false
PREFLIGHT=false
USE_EXISTING_RG=false
WRITE_ENV=""
SKIP_PRINT_SECRETS=false

usage() {
  cat <<EOF
Usage: $0 [options]

  --resource-group NAME
  --location REGION
  --acr-name NAME
  --cae-name NAME
  --app-name NAME
  --eventhub-ns NAME
  --eventhub-name NAME
  --use-existing-rg
  --skip-eventhub
  --preflight
  --write-env PATH
  --skip-print-secrets
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group)  RG="$2"; shift 2 ;;
    --location)        LOCATION="$2"; shift 2 ;;
    --acr-name)        ACR_NAME="$2"; shift 2 ;;
    --cae-name)        CAE_NAME="$2"; shift 2 ;;
    --app-name)        APP_NAME="$2"; shift 2 ;;
    --eventhub-ns)     EH_NS="$2"; shift 2 ;;
    --eventhub-name)   EH_NAME="$2"; shift 2 ;;
    --skip-eventhub)   SKIP_EVENTHUB=true; shift ;;
    --preflight)       PREFLIGHT=true; shift ;;
    --use-existing-rg) USE_EXISTING_RG=true; shift ;;
    --write-env)       WRITE_ENV="$2"; shift 2 ;;
    --skip-print-secrets) SKIP_PRINT_SECRETS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: Azure CLI not installed." >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  if [[ -n "${AZURE_CLIENT_ID:-}" && -n "${AZURE_CLIENT_SECRET:-}" && -n "${AZURE_TENANT_ID:-}" ]]; then
    az login --service-principal \
      -u "$AZURE_CLIENT_ID" \
      -p "$AZURE_CLIENT_SECRET" \
      --tenant "$AZURE_TENANT_ID" \
      --output none
    [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]] && az account set --subscription "$AZURE_SUBSCRIPTION_ID"
  else
    echo "ERROR: Not logged in. Use Cloud Shell, az login, or set AZURE_CLIENT_* env vars." >&2
    exit 1
  fi
fi

SUB_ID=$(az account show --query id -o tsv)
SUB_NAME=$(az account show --query name -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

[[ -z "$EH_NS" && "$SKIP_EVENTHUB" == "false" ]] && EH_NS="evhns-telemetry-dev"

echo ""
echo "Bootstrap"
echo "  subscription : $SUB_NAME ($SUB_ID)"
echo "  tenant       : $TENANT_ID"
echo "  rg           : $RG"
echo "  location     : $LOCATION"
echo "  acr          : $ACR_NAME"
echo "  cae          : $CAE_NAME"
echo "  app          : $APP_NAME"
if [[ "$SKIP_EVENTHUB" == "false" ]]; then
  echo "  eventhub     : $EH_NS / $EH_NAME"
else
  echo "  eventhub     : skipped"
fi
echo "  mode         : $([[ "$PREFLIGHT" == "true" ]] && echo preflight || echo apply)"
echo ""

required_providers=(
  Microsoft.ContainerRegistry
  Microsoft.OperationalInsights
  Microsoft.App
  Microsoft.EventHub
)

echo "[preflight]"
for ns in "${required_providers[@]}"; do
  state=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$state" != "Registered" ]]; then
    if [[ "$PREFLIGHT" == "true" ]]; then
      echo "  ! $ns ($state)"
    else
      echo "  register $ns"
      az provider register --namespace "$ns" --output none
    fi
  else
    echo "  ok $ns"
  fi
done

ACR_AVAIL=$(az acr check-name --name "$ACR_NAME" --query nameAvailable -o tsv 2>/dev/null || echo "false")
if [[ "$ACR_AVAIL" != "true" ]]; then
  if az acr show --name "$ACR_NAME" --resource-group "$RG" >/dev/null 2>&1; then
    echo "  ok acr $ACR_NAME (existing)"
  else
    echo "ERROR: ACR name '$ACR_NAME' unavailable — use --acr-name" >&2
    [[ "$PREFLIGHT" == "true" ]] || exit 1
  fi
else
  echo "  ok acr name available"
fi

[[ "$PREFLIGHT" == "true" ]] && { echo "Preflight complete."; exit 0; }

echo ""
if [[ "$USE_EXISTING_RG" == "true" ]]; then
  echo "[1/5] resource group (existing)"
  if ! az group show --name "$RG" >/dev/null 2>&1; then
    echo "ERROR: Resource group '$RG' not found." >&2
    exit 1
  fi
  RG_LOCATION=$(az group show --name "$RG" --query location -o tsv)
  [[ -n "$RG_LOCATION" ]] && LOCATION="$RG_LOCATION"
  echo "  $RG ($LOCATION)"
else
  echo "[1/5] resource group"
  az group create --name "$RG" --location "$LOCATION" --output none
  echo "  $RG"
fi

echo "[2/5] container registry"
if az acr show --name "$ACR_NAME" --resource-group "$RG" >/dev/null 2>&1; then
  echo "  reuse $ACR_NAME"
else
  az acr create \
    --name "$ACR_NAME" \
    --resource-group "$RG" \
    --sku Standard \
    --admin-enabled false \
    --output none
fi
ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RG" --query loginServer -o tsv)
echo "  $ACR_LOGIN_SERVER"

echo "[3/5] container apps environment"
az extension add --name containerapp --upgrade --yes --output none 2>/dev/null || true
if az containerapp env show --name "$CAE_NAME" --resource-group "$RG" >/dev/null 2>&1; then
  echo "  reuse $CAE_NAME"
else
  az containerapp env create \
    --name "$CAE_NAME" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    --output none
fi
echo "  $CAE_NAME"

EH_CONN=""
if [[ "$SKIP_EVENTHUB" == "false" ]]; then
  echo "[4/5] event hubs"
  if az eventhubs namespace show --name "$EH_NS" --resource-group "$RG" >/dev/null 2>&1; then
    echo "  reuse namespace $EH_NS"
  else
    az eventhubs namespace create \
      --name "$EH_NS" \
      --resource-group "$RG" \
      --location "$LOCATION" \
      --sku Standard \
      --capacity 1 \
      --output none
  fi
  if az eventhubs eventhub show --name "$EH_NAME" --namespace-name "$EH_NS" --resource-group "$RG" >/dev/null 2>&1; then
    echo "  reuse hub $EH_NAME"
  else
    az eventhubs eventhub create \
      --name "$EH_NAME" \
      --namespace-name "$EH_NS" \
      --resource-group "$RG" \
      --partition-count 4 \
      --retention-time 24 \
      --output none
  fi
  EH_CONN=$(az eventhubs namespace authorization-rule keys list \
    --resource-group "$RG" \
    --namespace-name "$EH_NS" \
    --name RootManageSharedAccessKey \
    --query primaryConnectionString -o tsv)
  echo "  $EH_NS / $EH_NAME"
else
  echo "[4/5] event hubs skipped"
fi

echo "[5/5] managed identity"
# No service principal is required. The Container App uses a system-assigned
# managed identity to pull images from ACR (already declared in
# infra/containerapp.template.yaml via registries[*].identity: system).
# The self-hosted CI runner VM authenticates to Azure via its own managed
# identity too (auth-type: IDENTITY in azure/login@v2 — no secret needed).

MI_PRINCIPAL_ID=""

if az containerapp show --name "$APP_NAME" --resource-group "$RG" >/dev/null 2>&1; then
  az containerapp identity assign \
    --name "$APP_NAME" \
    --resource-group "$RG" \
    --system-assigned \
    --output none

  MI_PRINCIPAL_ID=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RG" \
    --query identity.principalId -o tsv)

  az role assignment create \
    --assignee "$MI_PRINCIPAL_ID" \
    --role AcrPull \
    --scope "/subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/Microsoft.ContainerRegistry/registries/${ACR_NAME}" \
    --output none 2>/dev/null || true

  echo "  $APP_NAME  principal: $MI_PRINCIPAL_ID"
else
  echo "  Container App '$APP_NAME' not deployed yet."
  echo "  Its managed identity will be created on first deploy (see .github/workflows/deploy.yml)."
fi

# The self-hosted runner VM needs these two roles assigned to its managed
# identity so CI can push images and update the Container App without any
# AZURE_CREDENTIALS secret. Run once as a subscription Owner/Contributor:
#
#   RUNNER_MI=$(az vm show -g <runner-rg> -n <runner-vm> \
#                 --query identity.principalId -o tsv)
#   az role assignment create --assignee "$RUNNER_MI" --role AcrPush \
#     --scope /subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/\
# Microsoft.ContainerRegistry/registries/${ACR_NAME}
#   az role assignment create --assignee "$RUNNER_MI" \
#     --role "Container Apps Contributor" \
#     --scope /subscriptions/${SUB_ID}/resourceGroups/${RG}

if [[ -n "$WRITE_ENV" ]]; then
  mkdir -p "$(dirname "$WRITE_ENV")"
  cat > "$WRITE_ENV" <<EOF
AZURE_TENANT_ID=$TENANT_ID
AZURE_SUBSCRIPTION_ID=$SUB_ID
USE_EXISTING_RG=$USE_EXISTING_RG
AZURE_RESOURCE_GROUP=$RG
AZURE_LOCATION=$LOCATION
ACR_NAME=$ACR_NAME
ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER
CAE_NAME=$CAE_NAME
APP_NAME=$APP_NAME
EH_NS=$EH_NS
EVENTHUB_NAMESPACE=${EH_NS}.servicebus.windows.net
EVENTHUB_CONNECTION_STRING=$EH_CONN
EVENTHUB_NAME=$EH_NAME
EOF
  echo "  wrote $WRITE_ENV"
fi

if [[ "$SKIP_PRINT_SECRETS" != "true" ]]; then
  echo ""
  echo "GitHub secrets (repo → Settings → Secrets → Actions)"
  echo "  AZURE_SUBSCRIPTION_ID=$SUB_ID"
  echo "  AZURE_TENANT_ID=$TENANT_ID"
  echo "  AZURE_RESOURCE_GROUP=$RG"
  echo "  AZURE_LOCATION=$LOCATION"
  echo "  ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER"
  echo "  AZURE_ACR_NAME=$ACR_NAME"
  echo "  AZURE_CONTAINER_APP_NAME=$APP_NAME"
  echo "  AZURE_CAE_NAME=$CAE_NAME"
  [[ -n "$EH_CONN" ]] && echo "  EVENTHUB_NAMESPACE=${EH_NS}.servicebus.windows.net"
  [[ -n "$EH_CONN" ]] && echo "  EVENTHUB_CONNECTION_STRING=$EH_CONN"
  [[ -n "$WRITE_ENV" ]] && echo "  env file: $WRITE_ENV"
  echo ""
  echo "  NOTE: AZURE_CREDENTIALS is no longer required."
  echo "  Make sure the self-hosted runner VM has a system-assigned managed"
  echo "  identity with AcrPush (on the registry) and 'Container Apps"
  echo "  Contributor' (on the resource group). See the comment above."
  echo ""
  echo "Bootstrap complete."
fi
