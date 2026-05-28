# Azure Observability Platform

Multi-app observability on Azure. Applications publish events to a shared Event Hub;
Azure Data Explorer (ADX) is the query database.

## Architecture

```
App A ──┐
App B ──┼── Event Hubs (ai-telemetry-events) ── ADX (observability)
App C ──┘         │                                    │
                  ├── Log Analytics                      ├── AuditLog
                  ├── Managed Prometheus               ├── AppEvents
                  └── Managed Grafana                  └── ObservabilityLogs
```

## Bootstrap

```bash
cp azure/bootstrap-azure.env.example azure/bootstrap-azure.env
# edit subscription, resource group, globally unique names

./scripts/bootstrap-azure.sh --preflight
./scripts/bootstrap-azure.sh
cp .env.azure .env
./scripts/deploy-local.sh deploy
```

## Shared event envelope

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "event_type": "ai.request.end | app.log | app.metric | app.audit",
  "app_id": "my-app",
  "service_name": "my-app",
  "environment": "dev",
  "tenant_id": "team-or-client",
  "trace_id": "hex",
  "payload": {}
}
```

## Event types

| Type | ADX table |
|---|---|
| `ai.request.end` | `AuditLog` |
| `ai.prompt.log` | `PromptLog` |
| `app.log` | `ObservabilityLogs` |
| `app.*` | `AppEvents` |

## Onboard another app

1. Use the same Event Hub settings from bootstrap output.
2. Set `OBS_APP_ID` and `OTEL_SERVICE_NAME` to your app id.
3. Publish:

```python
from observability import EventHubPublisher, EVENT_APP_LOG

pub = EventHubPublisher()
pub.publish(EVENT_APP_LOG, {
    "level": "info",
    "logger": "billing-api",
    "message": "invoice generated",
}, tenant_id="finance")
pub.flush()
```

4. Register in ADX (optional):

```kql
.append AppRegistry <| datatable(app_id:string, display_name:string, team:string, domain:string, registered_at:datetime, active:bool)
[
  "billing-api", "Billing API", "Finance", "billing", datetime(2026-05-28), true
]
```

## ADX schema

After bootstrap, run `infra/adx-schema.kql` in the ADX query window against
database `observability`.

## SDK

| Module | Purpose |
|---|---|
| `observability/envelope.py` | Standard envelope |
| `observability/publisher.py` | Event Hub publisher |
| `observability/examples/publish_app_event.py` | Example |
