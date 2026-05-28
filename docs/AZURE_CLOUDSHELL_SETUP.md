# Azure Cloud Shell Setup (Beginner — One Command at a Time)

Open [Azure Cloud Shell](https://shell.azure.com) → choose **Bash** (not PowerShell).

Run each command below **one at a time**. Wait for it to finish before the next.  
If you see an error, stop and copy the error message.

Your settings:

| Setting | Value |
|---|---|
| Subscription | `az-uc-analytics-sandbox-eastus` |
| Subscription ID | `216d62c8-0f0c-4e5c-9cda-cc553e7ab186` |
| Resource group | `az03-al-titan-sandbox-rg` |

---

## Part A — Check you have access

### Command 1 — Select your subscription

```bash
az account set --subscription "216d62c8-0f0c-4e5c-9cda-cc553e7ab186"
```

**Success:** no output (that is normal).

---

### Command 2 — Confirm the resource group exists

```bash
az group show --name "az03-al-titan-sandbox-rg" -o table
```

**Success:** a table showing `az03-al-titan-sandbox-rg` and `eastus` (or similar region).

---

### Command 3 — Check your role (need Contributor)

```bash
az role assignment list --assignee "$(az ad signed-in-user show --query id -o tsv)" --resource-group "az03-al-titan-sandbox-rg" -o table
```

**Success:** a row with role **Contributor**.

**If you only see Reader:** ask your admin for Contributor, then stop here.

---

## Part B — Download the project

### Command 4 — Clone the repo from GitHub

```bash
git clone https://github.com/ajaykhannaus/observability.git
```

**Success:** `Cloning into 'observability'...`

---

### Command 5 — Go into the project folder

```bash
cd observability
```

**Success:** your prompt changes to show `observability`.

---

### Command 6 — Get the latest code

```bash
git pull
```

**Success:** `Already up to date.` or a list of updated files.

---

## Part C — Create bootstrap settings file

### Command 7 — Create `azure/bootstrap-azure.env`

Copy this **whole block** as one paste (it is one command):

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

**Success:** no output.

---

### Command 8 — Confirm the file was created

```bash
cat azure/bootstrap-azure.env
```

**Success:** you see your subscription and resource group names.

---

## Part D — Preflight (safe test, nothing created)

### Command 9 — Make scripts executable

```bash
chmod +x scripts/bootstrap-azure.sh infra/bootstrap.sh infra/adx-data-connection.sh
```

**Success:** no output.

---

### Command 10 — Run preflight check

```bash
./scripts/bootstrap-azure.sh --preflight
```

**Success:** ends with `preflight ok`.

**If ACR or Event Hub name is taken:** edit names in step 11, then re-run command 10.

---

### Command 11 (only if preflight failed on name conflict)

Change names by adding your initials, e.g. `aj`:

```bash
sed -i 's/acrtelemetrydev/acrtelemetrydevaj/g' azure/bootstrap-azure.env
```

```bash
sed -i 's/evhns-telemetry-dev/evhns-telemetry-devaj/g' azure/bootstrap-azure.env
```

```bash
sed -i 's/adxtelemetrydev/adxtelemetrydevaj/g' azure/bootstrap-azure.env
```

Then run command 10 again:

```bash
./scripts/bootstrap-azure.sh --preflight
```

---

## Part E — Full bootstrap (creates Azure resources)

### Command 12 — Run bootstrap (~15–25 minutes)

```bash
./scripts/bootstrap-azure.sh
```

**Success:** ends with `Done` and shows paths for env, grafana, adx.

**Wait:** do not close Cloud Shell until this finishes.

---

## Part F — ADX database tables (one-time)

### Command 13 — Show the schema file

```bash
cat infra/adx-schema.kql
```

**Success:** you see KQL table definitions.

---

### Command 14 — Apply schema in Azure Portal (manual)

1. Open [Azure Portal](https://portal.azure.com)
2. Search **Azure Data Explorer**
3. Open cluster **adxtelemetrydev** (or your name from bootstrap)
4. Click **Query**
5. Select database **observability**
6. Copy all text from command 13 and paste into the query window
7. Click **Run**

**Success:** tables like `AuditLog`, `AppEvents` are created.

---

## Part G — Copy secrets to your Mac

### Command 15 — Show your secrets file

```bash
cat .env.azure
```

**Success:** lines like `AZURE_CLIENT_ID=`, `EVENTHUB_CONNECTION_STRING=`, etc.

Copy everything, or use Cloud Shell **Download** on file `.env.azure`.

---

## Part H — Deploy from your Mac (after Cloud Shell)

Open **Terminal on your Mac** (not Cloud Shell). Run one at a time:

### Command 16 — Go to your project

```bash
cd /Users/mac/Documents/CompanyWork/EXLData/Telemetry
```

---

### Command 17 — Save secrets (if you downloaded `.env.azure`)

```bash
cp .env.azure .env
```

Or paste Cloud Shell output into `.env` manually.

---

### Command 18 — Make deploy scripts executable

```bash
chmod +x scripts/deploy-local.sh scripts/azure-local-login.sh
```

---

### Command 19 — Login with Service Principal (from `.env`)

```bash
./scripts/deploy-local.sh login
```

**Success:** `Azure CLI authenticated as SP ...`

---

### Command 20 — Deploy the app

```bash
./scripts/deploy-local.sh deploy
```

**Success:** deploy complete message.

---

### Command 21 — Import Grafana dashboard

```bash
./scripts/deploy-local.sh grafana
```

---

### Command 22 — Verify everything works

```bash
./scripts/deploy-local.sh verify
```

**Success:** health checks pass.

---

## Quick checklist

| Step | Command | Done? |
|---|---|---|
| 1 | `az account set ...` | ☐ |
| 2 | `az group show ...` | ☐ |
| 3 | `az role assignment list ...` | ☐ |
| 4 | `git clone ...` | ☐ |
| 5 | `cd observability` | ☐ |
| 6 | `git pull` | ☐ |
| 7 | `cat > azure/bootstrap-azure.env ...` | ☐ |
| 10 | `./scripts/bootstrap-azure.sh --preflight` | ☐ |
| 12 | `./scripts/bootstrap-azure.sh` | ☐ |
| 14 | ADX schema in Portal | ☐ |
| 15 | `cat .env.azure` | ☐ |
| 19–22 | deploy from Mac | ☐ |

---

## If something fails

| Error | What to do |
|---|---|
| `Authorization failed` | Re-run command 3 — need Contributor |
| ACR name not available | Run command 11, then 10 again |
| `git clone` fails | Check internet / try again |
| Bootstrap stuck on ADX | Normal — wait up to 10 minutes |
| Mac deploy fails | Make sure `.env` has all values from `.env.azure` |

---

## Related files

| File | Purpose |
|---|---|
| `azure/bootstrap-azure.env` | Your Azure settings (created in step 7) |
| `.env.azure` | Secrets output after bootstrap (step 15) |
| `docs/AZURE_OBSERVABILITY.md` | How other apps connect later |
