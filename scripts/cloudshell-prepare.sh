#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Cloud Shell bootstrap helper ==="
echo ""

if [[ ! -f azure/bootstrap-azure.env ]]; then
  echo "Copying azure/bootstrap-azure.sandbox.env → azure/bootstrap-azure.env"
  cp azure/bootstrap-azure.sandbox.env azure/bootstrap-azure.env
else
  echo "Using existing azure/bootstrap-azure.env"
fi

chmod +x scripts/bootstrap-azure.sh infra/bootstrap.sh infra/adx-data-connection.sh

echo ""
echo "Next commands (run one at a time):"
echo ""
echo "  ./scripts/bootstrap-azure.sh --preflight"
echo "  ./scripts/bootstrap-azure.sh"
echo "  cat .env.azure"
echo ""
