# Azure Cloud Shell Setup

Run in **Bash** from [Azure Cloud Shell](https://shell.azure.com). You are already authenticated — no `az login` required.

After bootstrap, deploy from your Mac using `.env.azure` (secrets stay local, not in GitHub).

---

## Prerequisites

- **Contributor** on your resource group (not subscription-wide RG create permission)
- Bash Cloud Shell (not PowerShell-only session)

---

## Step 1 — Verify access

```bash
az account set --subscription "216d62c8-0f0c-4e5c-9cda-cc553e7ab186"
az group show --name "az03-al-titan-sandbox-rg" -o table
```

Check your role on the resource group (need **Contributor** to run bootstrap):

```bash
az account set --subscription "216d62c8-0f0c-4e5c-9cda-cc553e7ab186"
az role assignment list \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --resource-group "az03-al-titan-sandbox-rg" \
  -o table
```

If you only see **Reader** (or no rows), ask your admin for **Contributor** on the resource group, or have them run bootstrap and send you `.env.azure`.

Replace subscription ID and resource group if yours differ.

---

## Step 2 — Get the repo

**From GitHub:**

```bash
git clone https://github.com/ajaykhannaus/observability.git
cd observability
```

**From your Mac:** upload the project folder (or zip) via the Cloud Shell **Upload** button, then:

```bash
cd Telemetry
```

---

## Step 3 — Bootstrap config

If `azure/bootstrap-azure.env` is not in the repo, create it:

```bash
cat > azure/bootstrap-azure.env <<'EOF'
AZURE_SUBSCRIPTION_NAME=az-uc-analytics-sandbox-eastus
AZURE_SUBSCRIPTION_ID=216d62c8-0f0c-4e5c-9cda-cc553e7ab186
USE_EXISTING_RG=true
AZURE_RESOURCE_GROUP=az03-al-titan-sandbox-rg
AZURE_LOCATION=eastus
ACR_NAME=acrtelemetrydev
CAE_NAME=cae-telemetry-dev
APP_NAME=ai-telemetry-runner-dev
PROM_APP_NAME=prometheus-scraper-dev
GRAFANA_NAME=grafana-telemetry-dev
PROM_WS=telemetry-prometheus-dev
EH_NS=evhns-telemetry-dev
EVENTHUB_NAME=ai-telemetry-events
PROVISION_OBSERVABILITY=true
PROVISION_ADX=true
ADX_CLUSTER=adxtelemetrydev
ADX_DATABASE=observability
ADX_ENV=dev
BUILD_IMAGES=true
WRITE_ENV_FILE=.env.azure
EOF
```

If ACR or Event Hub names are taken globally, edit before bootstrap:

```bash
# Example — add your initials
sed -i 's/acrtelemetrydev/acrtelemetrydevYOURINITIALS/g' azure/bootstrap-azure.env
sed -i 's/evhns-telemetry-dev/evhns-telemetry-devYOURINITIALS/g' azure/bootstrap-azure.env
```

---

## Step 4 — Preflight (check only)

```bash
chmod +x scripts/bootstrap-azure.sh infra/bootstrap.sh infra/adx-data-connection.sh
./scripts/bootstrap-azure.sh --preflight
```

---

## Step 5 — Full bootstrap (~15–25 min)

Creates ACR, Container Apps environment, Event Hubs, Managed Prometheus, Grafana, ADX, builds images, writes `.env.azure`.

```bash
./scripts/bootstrap-azure.sh
```

Skip image build if you only want infrastructure:

```bash
./scripts/bootstrap-azure.sh --no-build
```

---

## Step 6 — ADX schema (one-time)

1. Azure Portal → **Azure Data Explorer** → cluster `adxtelemetrydev`
2. Query → database `observability`
3. Paste and run `infra/adx-schema.kql`

Preview schema in Cloud Shell:

```bash
cat infra/adx-schema.kql
```

---

## Step 7 — Copy secrets to your Mac

```bash
cat .env.azure
```

Download the file via Cloud Shell **Download**, or copy the output into your local `.env`.

---

## Step 8 — Deploy from your Mac

```bash
cd /path/to/Telemetry
cp .env.azure .env

chmod +x scripts/deploy-local.sh scripts/azure-local-login.sh
./scripts/deploy-local.sh login
./scripts/deploy-local.sh deploy
./scripts/deploy-local.sh grafana
./scripts/deploy-local.sh verify
```

---

## What bootstrap creates

| Resource | Name |
|---|---|
| Resource group | `az03-al-titan-sandbox-rg` (existing) |
| Container Registry | `acrtelemetrydev` |
| Container Apps environment | `cae-telemetry-dev` |
| Container App | `ai-telemetry-runner-dev` |
| Event Hub namespace / hub | `evhns-telemetry-dev` / `ai-telemetry-events` |
| Managed Prometheus | `telemetry-prometheus-dev` |
| Managed Grafana | `grafana-telemetry-dev` |
| ADX cluster / database | `adxtelemetrydev` / `observability` |
| Log Analytics | auto-created with Container Apps |

---

## Verify after deploy

**Cloud Shell:**

```bash
az containerapp show \
  --name ai-telemetry-runner-dev \
  --resource-group az03-al-titan-sandbox-rg \
  --query "properties.runningStatus" -o tsv
```

**Mac:**

```bash
./scripts/deploy-local.sh verify
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Authorization failed` | Run the role check in Step 1; ask admin for **Contributor** on the resource group |
| ACR name not available | Change `ACR_NAME` in `azure/bootstrap-azure.env` |
| Event Hub name not available | Change `EH_NS` in `azure/bootstrap-azure.env` |
| ADX cluster slow | Normal — can take 5–10 minutes |
| `Microsoft.App not registered` | Bootstrap registers providers; re-run bootstrap |
| Lost `.env.azure` | Re-run `./scripts/bootstrap-azure.sh` (idempotent) |

---

## Related files

| File | Purpose |
|---|---|
| `azure/bootstrap-azure.env` | Bootstrap settings (gitignored) |
| `scripts/bootstrap-azure.sh` | Full Azure provisioning |
| `infra/adx-schema.kql` | ADX tables and routing policies |
| `scripts/deploy-local.sh` | Deploy from Mac using `.env` |
| `docs/AZURE_OBSERVABILITY.md` | Multi-app observability guide |
