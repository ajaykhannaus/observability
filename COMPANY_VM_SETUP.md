# AI Gateway Telemetry — Company VM Setup Guide

Complete instructions for deploying this project on a company Azure VM with GitHub Actions CI/CD.

**Repo:** https://github.com/ajaykhannaus/azure-telemetry-llm (private)

---

## Overview

```
You (push code)
      │
      ▼
GitHub (ajaykhannaus/azure-telemetry-llm)
      │
      ▼  triggers job on self-hosted runner
Company Windows Server VM  ←── inside VPN ──→  Company Azure
      │                                              │
      │  docker build + push                   Azure Container Registry
      │  az containerapp update               Azure Container Apps (always-on)
      │                                              │
      │                                    ┌─────────┼──────────┐
      │                                    ▼         ▼          ▼
      │                               Event Hubs  :8000/metrics  Log Analytics
      │                                              │
      │                                           Grafana
      ▼
  Done — every push auto-deploys
```

---

## Part 1 — One-time Azure setup (run from any machine on VPN)

### 1.1 — Log in to company Azure

```powershell
az login --tenant <company-tenant-id>
az account set --subscription <company-subscription-id>
```

### 1.2 — Run the bootstrap script

```powershell
git clone https://github.com/ajaykhannaus/azure-telemetry-llm.git
cd azure-telemetry-llm

# Run bootstrap — creates resource group, ACR, Container Apps Environment, Service Principal
.\infra\bootstrap.sh `
  --resource-group  rg-ai-telemetry-dev `
  --location        eastus `
  --acr-name        <your-acr-name>          # e.g. acrcompanyprod  (globally unique)
  --cae-name        cae-telemetry-prod `
  --app-name        ai-telemetry-runner
```

> On Windows, if bash isn't available, use WSL or Git Bash.

The script will **print all 7 GitHub secrets** at the end — copy them, you'll need them in Part 2.

---

## Part 2 — GitHub Secrets (set once per repo)

Go to: **https://github.com/ajaykhannaus/azure-telemetry-llm/settings/secrets/actions**

Set all 7 secrets:

| Secret | Where to get it | Example |
|---|---|---|
| `AZURE_CREDENTIALS` | Printed by bootstrap.sh (JSON block) | `{"clientId":"...","clientSecret":"...","tenantId":"...","subscriptionId":"..."}` |
| `AZURE_RESOURCE_GROUP` | What you passed to bootstrap.sh | `rg-ai-telemetry-dev` |
| `ACR_LOGIN_SERVER` | Printed by bootstrap.sh | `acrcompanyprod.azurecr.io` |
| `AZURE_ACR_NAME` | Short name only | `acrcompanyprod` |
| `ACR_PASSWORD` | Printed by bootstrap.sh | `az acr credential show --name acrcompanyprod` |
| `AZURE_CONTAINER_APP_NAME` | ✅ Already set | `ai-telemetry-runner` |
| `AZURE_CAE_NAME` | What you passed to bootstrap.sh | `cae-telemetry-prod` |

---

## Part 3 — Set up the Windows Server VM as a GitHub Actions runner

### 3.1 — Get a runner registration token (expires in 1 hour)

Go to: **https://github.com/ajaykhannaus/azure-telemetry-llm/settings/actions/runners/new**

Select **Windows** → Copy the token shown (looks like `AABC...XYZ`)

### 3.2 — RDP into the company VM and run setup

Open **PowerShell as Administrator** and run:

```powershell
# Step A: Clone the repo
git clone https://github.com/ajaykhannaus/azure-telemetry-llm.git
cd azure-telemetry-llm

# Step B: Run the setup script
Set-ExecutionPolicy RemoteSigned -Scope Process -Force

.\infra\setup-runner.ps1 `
  -GitHubRepo    "ajaykhannaus/azure-telemetry-llm" `
  -GitHubToken   "<paste token from Step 3.1>" `
  -RunnerName    "company-vm-runner"
```

**What the script does automatically:**
- Installs Chocolatey (package manager)
- Installs Azure CLI
- Installs Docker Engine (for building Linux container images)
- Installs Git for Windows
- Installs Python 3.11
- Downloads GitHub Actions runner v2.317.0
- Registers this VM with the GitHub repo
- Installs the runner as a **Windows Service** (auto-starts on VM reboot)

### 3.3 — Verify the runner is online

Go to: **https://github.com/ajaykhannaus/azure-telemetry-llm/settings/actions/runners**

You should see `company-vm-runner` with a green **Idle** status.

### 3.4 — Verify Docker is in Linux container mode

In PowerShell on the VM:

```powershell
docker version
# Server OS/Arch should show: linux/amd64
# If it shows windows/amd64, switch with:
& "C:\Program Files\Docker\Docker\DockerCli.exe" -SwitchDaemon
```

---

## Part 4 — Deploy

### 4.1 — First deploy (after secrets are set)

From your machine (or directly from the VM):

```powershell
git clone https://github.com/ajaykhannaus/azure-telemetry-llm.git
cd azure-telemetry-llm
git push origin master
```

Or trigger manually: **GitHub → Actions → Build and Deploy to Azure → Run workflow**

### 4.2 — Watch the deploy

Go to: **https://github.com/ajaykhannaus/azure-telemetry-llm/actions**

You will see:
- `build-fn` — runs on company VM ✅
- `build-and-deploy-runner` — runs on company VM ✅
- `build-and-deploy-prometheus` — optional, skips if not set up
- `deploy-grafana-dashboard` — optional, skips if not set up

### 4.3 — Verify it's running

```powershell
# From the VM (on VPN), confirm the Container App is up
az containerapp show `
  --name ai-telemetry-runner `
  --resource-group rg-ai-telemetry-dev `
  --query "{status:properties.runningStatus, fqdn:properties.configuration.ingress.fqdn}" `
  -o json

# Hit the metrics endpoint
$fqdn = az containerapp show --name ai-telemetry-runner --resource-group rg-ai-telemetry-dev --query "properties.configuration.ingress.fqdn" -o tsv
curl "https://$fqdn/metrics"

# View live logs
az containerapp logs show `
  --name ai-telemetry-runner `
  --resource-group rg-ai-telemetry-dev `
  --tail 20 --follow
```

---

## Part 5 — Day-to-day usage

### Push a change

```powershell
git add .
git commit -m "your change"
git push origin master
# → GitHub notifies the VM → VM builds + deploys automatically
```

### View logs in Azure Portal

1. Azure Portal → Log Analytics workspace (auto-created with Container Apps Env)
2. Paste in Logs query:
```kql
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ai-telemetry-runner"
| order by TimeGenerated desc
| take 50
```

### Stop/Start the runner service (if needed)

```powershell
# On the company VM
Stop-Service actions.runner.*
Start-Service actions.runner.*
```

### Update the runner token (if it expires)

```powershell
cd C:\actions-runner
.\config.cmd remove --token <old-token>
# Then re-run setup-runner.ps1 with a new token
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Runner shows **Offline** in GitHub | RDP to VM → `Start-Service actions.runner.*` |
| `az acr login` fails on VM | Run `az login --tenant <tenant-id>` on the VM first |
| Docker build fails: `linux/amd64` not supported | Switch to Linux containers — see Step 3.4 |
| `az containerapp` command not found | Run `az extension add --name containerapp --upgrade --yes` |
| Push fails with 403 | Token expired — generate a new PAT at github.com/settings/tokens |
| Container App keeps restarting | Check logs: `az containerapp logs show --name ai-telemetry-runner --resource-group rg-ai-telemetry-dev --tail 50` |

---

## File reference

```
azure-telemetry-llm/
├── .github/workflows/deploy.yml    ← CI/CD pipeline (jobs 1&2 run on company VM)
├── infra/
│   ├── bootstrap.sh                ← creates all Azure resources (run once)
│   └── setup-runner.ps1            ← installs prereqs + registers VM as runner
├── generator/                      ← telemetry generator (runs as Container App)
├── dashboards/grafana_dashboard.json
├── .env.example                    ← copy to .env for local dev
└── README.md                       ← full architecture + env variable docs
```
