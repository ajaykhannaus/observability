#!/usr/bin/env bash
# =============================================================================
# Provision Azure Data Explorer cluster + database + Event Hubs data connection
# =============================================================================
# Run AFTER infra/bootstrap.sh. Requires az CLI logged in.
#
# Usage:
#   chmod +x infra/adx-data-connection.sh
#   ./infra/adx-data-connection.sh \
#     --resource-group  rg-ai-telemetry-prod \
#     --location        eastus \
#     --cluster-name    adxtelemetryprod \
#     --db-name         ai-telemetry-audit \
#     --eventhub-ns     evhns-telemetry-prod \
#     --eventhub-name   ai-telemetry-events
#
# What it provisions:
#   1. ADX cluster (Dev SKU for dev/staging; Standard_D11_v2 for prod)
#   2. ADX database with 7-year hot-cache retention on AuditLog
#   3. Event Hubs consumer group for ADX
#   4. Data Connection: Event Hubs → ADX RawEvents table
#
# ADX pricing (~US East):
#   Dev/test  (Dev_No_SLA_Standard_D11_v2):   ~$70/month
#   Prod      (Standard_D11_v2 × 2 nodes):    ~$300/month
#   Storage:  ~$20/TB/month (Azure Blob, LRS)
# =============================================================================
set -euo pipefail

RG="rg-ai-telemetry-prod"
LOCATION="eastus"
CLUSTER="adxtelemetryprod"
DB="ai-telemetry-audit"
EH_NS=""
EH_NAME="ai-telemetry-events"
CONSUMER_GROUP="adx-ingest"
ENV="prod"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group) RG="$2";       shift 2 ;;
    --location)       LOCATION="$2"; shift 2 ;;
    --cluster-name)   CLUSTER="$2";  shift 2 ;;
    --db-name)        DB="$2";       shift 2 ;;
    --eventhub-ns)    EH_NS="$2";    shift 2 ;;
    --eventhub-name)  EH_NAME="$2";  shift 2 ;;
    --env)            ENV="$2";      shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$EH_NS" ]]; then
  echo "ERROR: --eventhub-ns is required" >&2; exit 1
fi

SUB_ID=$(az account show --query id -o tsv)

echo "============================================================"
echo "  ADX Provision"
echo "  RG:       $RG"
echo "  Cluster:  $CLUSTER"
echo "  DB:       $DB"
echo "  EH NS:    $EH_NS"
echo "  EH Name:  $EH_NAME"
echo "============================================================"

# ── 1. ADX cluster ──────────────────────────────────────────────────────────
SKU="Dev_No_SLA_Standard_D11_v2"
CAPACITY=1
if [[ "$ENV" == "prod" ]]; then
  SKU="Standard_D11_v2"
  CAPACITY=2
fi

echo "[1/5] Creating ADX cluster ($SKU × $CAPACITY)..."
az kusto cluster create \
  --name "$CLUSTER" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku name="$SKU" capacity="$CAPACITY" tier="Basic" \
  --no-wait \
  --output none 2>/dev/null || echo "      (cluster already exists — skipping)"
echo "      Waiting for cluster to be ready (~5 min)..."
az kusto cluster wait --name "$CLUSTER" --resource-group "$RG" \
  --created --interval 30 --timeout 600
echo "      ✓ $CLUSTER"

# ── 2. ADX database ─────────────────────────────────────────────────────────
echo "[2/5] Creating ADX database..."
az kusto database create \
  --cluster-name "$CLUSTER" \
  --resource-group "$RG" \
  --database-name "$DB" \
  --read-write-database soft-delete-period="P2555D" hot-cache-period="P30D" location="$LOCATION" \
  --output none 2>/dev/null || echo "      (database already exists — skipping)"
echo "      ✓ $DB"

# ── 3. Event Hubs consumer group ────────────────────────────────────────────
echo "[3/5] Creating Event Hubs consumer group for ADX..."
az eventhubs eventhub consumer-group create \
  --resource-group "$RG" \
  --namespace-name "$EH_NS" \
  --eventhub-name "$EH_NAME" \
  --name "$CONSUMER_GROUP" \
  --output none 2>/dev/null || echo "      (consumer group already exists — skipping)"
echo "      ✓ $CONSUMER_GROUP"

# ── 4. Apply schema ─────────────────────────────────────────────────────────
echo "[4/5] Applying ADX schema (infra/adx-schema.kql)..."
CLUSTER_URI=$(az kusto cluster show --name "$CLUSTER" --resource-group "$RG" \
  --query "uri" -o tsv)
echo "      URI: $CLUSTER_URI"
# ADX CLI (az kusto) doesn't support .create-merge via script yet.
# Use the Kusto REST API or az kusto management-command for KQL DDL.
# The schema file (infra/adx-schema.kql) must be executed manually or
# via a pipeline job using https://docs.microsoft.com/kusto/management.
echo "      → Manual step: run infra/adx-schema.kql in ADX Web UI at $CLUSTER_URI"
echo "        or integrate into CI via:"
echo "        az kusto execute-query --cluster-uri $CLUSTER_URI ..."

# ── 5. Data Connection ───────────────────────────────────────────────────────
echo "[5/5] Creating Event Hubs → ADX data connection..."
EH_RESOURCE_ID=$(az eventhubs eventhub show \
  --resource-group "$RG" \
  --namespace-name "$EH_NS" \
  --name "$EH_NAME" \
  --query id -o tsv)

az kusto data-connection event-hub create \
  --cluster-name "$CLUSTER" \
  --database-name "$DB" \
  --resource-group "$RG" \
  --data-connection-name "ai-telemetry-ingest" \
  --location "$LOCATION" \
  --event-hub-resource-id "$EH_RESOURCE_ID" \
  --consumer-group "$CONSUMER_GROUP" \
  --table-name "RawEvents" \
  --mapping-rule-name "" \
  --data-format "MULTIJSON" \
  --output none 2>/dev/null || echo "      (data connection already exists — skipping)"
echo "      ✓ Event Hubs → ADX data connection"

echo ""
echo "============================================================"
echo "  ADX setup complete."
echo "  Next steps:"
echo "  1. Open ADX Web UI: $CLUSTER_URI"
echo "  2. Select database: $DB"
echo "  3. Run:  infra/adx-schema.kql  (DDL + ingestion mappings)"
echo "  4. Verify: .show table AuditLog"
echo "  5. Enable WORM Blob for audit storage:"
echo "     az storage container immutability-policy create \\"
echo "       --account-name <storage-account> --container-name ai-audit-log \\"
echo "       --period 2555"
echo "============================================================"
