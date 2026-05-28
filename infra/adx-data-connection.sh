#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

RG="rg-telemetry-dev"
LOCATION="eastus"
CLUSTER="adxtelemetrydev"
DB="observability"
EH_NS=""
EH_NAME="ai-telemetry-events"
CONSUMER_GROUP="adx-ingest"
ENV="dev"
DATA_CONNECTION="observability-ingest"

usage() {
  cat <<EOF
Usage: $0 [options]

  --resource-group NAME
  --location REGION
  --cluster-name NAME
  --db-name NAME
  --eventhub-ns NAME
  --eventhub-name NAME
  --consumer-group NAME
  --data-connection NAME
  --env dev|prod
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group) RG="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --cluster-name) CLUSTER="$2"; shift 2 ;;
    --db-name) DB="$2"; shift 2 ;;
    --eventhub-ns) EH_NS="$2"; shift 2 ;;
    --eventhub-name) EH_NAME="$2"; shift 2 ;;
    --consumer-group) CONSUMER_GROUP="$2"; shift 2 ;;
    --data-connection) DATA_CONNECTION="$2"; shift 2 ;;
    --env) ENV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ -n "$EH_NS" ]] || { echo "ERROR: --eventhub-ns required" >&2; exit 1; }

if ! az account show >/dev/null 2>&1; then
  echo "ERROR: Not logged in to Azure." >&2
  exit 1
fi

SKU="Dev_No_SLA_Standard_D11_v2"
CAPACITY=1
[[ "$ENV" == "prod" ]] && SKU="Standard_D11_v2" && CAPACITY=2

echo "ADX provision"
echo "  rg       : $RG"
echo "  cluster  : $CLUSTER"
echo "  database : $DB"
echo "  eventhub : $EH_NS / $EH_NAME"
echo ""

echo "[1/4] cluster"
if az kusto cluster show --name "$CLUSTER" --resource-group "$RG" >/dev/null 2>&1; then
  echo "  reuse $CLUSTER"
else
  az kusto cluster create \
    --name "$CLUSTER" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    --sku name="$SKU" capacity="$CAPACITY" tier="Basic" \
    --output none
  az kusto cluster wait --name "$CLUSTER" --resource-group "$RG" --created --interval 30 --timeout 900
fi

CLUSTER_URI=$(az kusto cluster show --name "$CLUSTER" --resource-group "$RG" --query uri -o tsv)
echo "  $CLUSTER_URI"

echo "[2/4] database"
if az kusto database show --cluster-name "$CLUSTER" --resource-group "$RG" --database-name "$DB" >/dev/null 2>&1; then
  echo "  reuse $DB"
else
  az kusto database create \
    --cluster-name "$CLUSTER" \
    --resource-group "$RG" \
    --database-name "$DB" \
    --read-write-database soft-delete-period="P2555D" hot-cache-period="P30D" location="$LOCATION" \
    --output none
fi

echo "[3/4] event hub consumer group"
az eventhubs eventhub consumer-group create \
  --resource-group "$RG" \
  --namespace-name "$EH_NS" \
  --eventhub-name "$EH_NAME" \
  --name "$CONSUMER_GROUP" \
  --output none 2>/dev/null || echo "  reuse $CONSUMER_GROUP"

echo "[4/4] data connection"
EH_RESOURCE_ID=$(az eventhubs eventhub show \
  --resource-group "$RG" \
  --namespace-name "$EH_NS" \
  --name "$EH_NAME" \
  --query id -o tsv)

if az kusto data-connection event-hub show \
  --cluster-name "$CLUSTER" \
  --database-name "$DB" \
  --resource-group "$RG" \
  --data-connection-name "$DATA_CONNECTION" >/dev/null 2>&1; then
  echo "  reuse $DATA_CONNECTION"
else
  az kusto data-connection event-hub create \
    --cluster-name "$CLUSTER" \
    --database-name "$DB" \
    --resource-group "$RG" \
    --data-connection-name "$DATA_CONNECTION" \
    --location "$LOCATION" \
    --event-hub-resource-id "$EH_RESOURCE_ID" \
    --consumer-group "$CONSUMER_GROUP" \
    --table-name "RawEvents" \
    --mapping-rule-name "" \
    --data-format "MULTIJSON" \
    --output none
fi

SCHEMA_FILE="$ROOT/infra/adx-schema.kql"
if [[ -f "$SCHEMA_FILE" ]]; then
  echo ""
  echo "Schema: run in ADX query window ($CLUSTER_URI)"
  echo "  database: $DB"
  echo "  file: infra/adx-schema.kql"
fi

echo ""
echo "Done"
echo "  ADX_CLUSTER_URI=$CLUSTER_URI"
echo "  ADX_DATABASE=$DB"
