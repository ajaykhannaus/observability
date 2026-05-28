# Azure Cloud Shell Setup

Run this guide in **Bash** from **Azure Cloud Shell** or **VS Code for the Web on Azure**. You are already logged in — no `az login` or GitHub secrets required.

**Repo:** https://github.com/ajaykhannaus/obserability

---

## Quick start

```bash
git clone https://github.com/ajaykhannaus/obserability.git
cd obserability

# Optional — if the wrong subscription is selected
export AZURE_SUBSCRIPTION_ID="<your-subscription-guid>"

chmod +x scripts/azure-cloudshell-setup.sh
./scripts/azure-cloudshell-setup.sh
```

---

## What the script creates (dev names)

| Resource | Name |
|---|---|
| Resource group | `rg-ai-telemetry-dev` |
| Azure Container Registry | `acrtelemetrydev` |
| Container Apps environment | `cae-telemetry-dev` |
| Telemetry runner app (name only) | `ai-telemetry-runner-dev` |
| Event Hub namespace | `evhns-telemetry-dev` |
| Event hub | `ai-telemetry-events` |

The script also:

1. Shows / sets your Azure subscription  
2. Registers required Azure resource providers  
3. Creates the resource group  
4. Runs `infra/bootstrap.sh` (ACR, Container Apps env, Event Hubs, Service Principal for CI)

At the end, **copy the printed GitHub secrets block** if you plan to use GitHub Actions later. For local-only deploy, save Event Hub values in your machine’s `.env` instead.

---

## Create resource group only (manual)

If you only want the resource group without full bootstrap:

```bash
az account set --subscription "<your-subscription-guid>"

RG="rg-ai-telemetry-dev"
LOCATION="eastus"

az group create \
  --name "$RG" \
  --location "$LOCATION" \
  --tags project=obserability environment=dev

az group show --name "$RG" -o table
```

---

## Register providers (manual, one-time per subscription)

```bash
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.EventHub \
          Microsoft.Monitor Microsoft.Dashboard Microsoft.OperationalInsights; do
  az provider register --namespace "$ns" --wait
done
```

---

## Build Docker images in Cloud Shell

Cloud Shell has no local Docker. Use **ACR cloud build** after bootstrap:

```bash
ACR=acrtelemetrydev

az acr build --registry $ACR --image ai-telemetry-runner:latest -f Dockerfile.runner .
az acr build --registry $ACR --image prometheus-scraper:latest -f Dockerfile.prometheus .
```

---

## Deploy and Grafana (from your Mac)

Keep secrets in local `.env` only (never GitHub):

```bash
cp .env.example .env
# Fill in AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
# Copy EVENTHUB_* from bootstrap output

./scripts/deploy-local.sh deploy
./scripts/deploy-local.sh grafana
./scripts/deploy-local.sh verify
```

For full provisioning including Managed Grafana and Prometheus:

```bash
./scripts/deploy-local.sh provision
./scripts/deploy-local.sh all
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Authorization failed` | Ask Azure admin for **Contributor** on the subscription or resource group |
| `ACR name not available` | Pick a globally unique name: `export ACR_NAME=acrtelemetrydev<yourinitials>` before running the script |
| `Microsoft.App not registered` | Re-run provider registration (see above) |
| `az: command not found` | Use **Bash** Cloud Shell, not a plain terminal without Azure CLI |
| Bootstrap SP JSON lost | Re-run `./infra/bootstrap.sh` — it reuses or resets the Service Principal |

---

## Related files

| File | Purpose |
|---|---|
| `scripts/azure-cloudshell-setup.sh` | Automated Cloud Shell setup |
| `infra/bootstrap.sh` | Creates ACR, CAE, Event Hubs, SP |
| `scripts/deploy-local.sh` | Local deploy with dev names (secrets in `.env`) |
| `.env.example` | Template for local secrets |
