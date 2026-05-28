#!/usr/bin/env bash
# Authenticate Azure CLI from local .env (no interactive az login, no GitHub secrets).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example to .env and fill in credentials." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AZURE_CLIENT_ID:?Set AZURE_CLIENT_ID in .env}"
: "${AZURE_CLIENT_SECRET:?Set AZURE_CLIENT_SECRET in .env}"
: "${AZURE_TENANT_ID:?Set AZURE_TENANT_ID in .env}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID in .env}"

az login --service-principal \
  -u "$AZURE_CLIENT_ID" \
  -p "$AZURE_CLIENT_SECRET" \
  --tenant "$AZURE_TENANT_ID" \
  --output none

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
echo "Azure CLI authenticated as SP $AZURE_CLIENT_ID (subscription $AZURE_SUBSCRIPTION_ID)"
