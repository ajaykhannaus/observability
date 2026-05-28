# Azure Cloud Shell Setup

Run this guide in **Bash** from **Azure Cloud Shell** or **VS Code for the Web on Azure**. You are already logged in — no `az login` or GitHub secrets required.

**Repo:** https://github.com/ajaykhannaus/observability

---

## Using an existing resource group (no RG create permission)

If your admin already gave you a resource group, **do not create a new one**. Set your RG name and use `--use-existing-rg`. The script reads the **location from that RG** automatically.

```bash
git clone https://github.com/ajaykhannaus/observability.git
cd observability

export AZURE_SUBSCRIPTION_ID="<your-subscription-guid>"
export AZURE_RESOURCE_GROUP="<your-company-rg-name>"   # e.g. rg-myteam-dev
export USE_EXISTING_RG=true

# Optional — pick globally unique names if defaults are taken
export ACR_NAME=acrtelemetrydev<yourinitials>
export EH_NS=evhns-telemetry-dev<yourinitials>

chmod +x scripts/azure-cloudshell-setup.sh
./scripts/azure-cloudshell-setup.sh
```

**Verify access first:**

```bash
az account set --subscription "<your-subscription-guid>"
az group show --name "<your-company-rg-name>" -o table
```

You need **Contributor** (or equivalent) **on that resource group** to create ACR, Container Apps, Event Hubs, etc. You do **not** need subscription-level RG create permission.

**Manual bootstrap only** (same RG, no RG create):

```bash
./infra/bootstrap.sh \
  --use-existing-rg \
  --resource-group "<your-company-rg-name>" \
  --acr-name       acrtelemetrydev \
  --cae-name       cae-telemetry-dev \
  --app-name       ai-telemetry-runner-dev \
  --eventhub-ns    evhns-telemetry-dev
```

---

## Quick start (create new resource group)

If you **can** create resource groups, use the default flow:

```bash
git clone https://github.com/ajaykhannaus/observability.git
cd observability

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
  --tags project=observability environment=dev

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
| `Authorization failed` on RG create | Use **existing RG**: `export USE_EXISTING_RG=true` and `AZURE_RESOURCE_GROUP=<admin-rg>` |
| `Authorization failed` on ACR/CAE | Ask admin for **Contributor** on the resource group, not just Reader |
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
