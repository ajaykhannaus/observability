#!/usr/bin/env python3
"""Configure local or Azure Grafana: datasources + import all dashboards into AI Telemetry folder."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS_DIR = ROOT / "dashboards"
GRAFANA = os.getenv("GRAFANA_URL", "http://localhost:3000").rstrip("/")
FOLDER_UID = "ai-telemetry-folder"
FOLDER_TITLE = "AI Telemetry"
AUTH = base64.b64encode(
    f"{os.getenv('GRAFANA_ADMIN_USER', 'admin')}:{os.getenv('GRAFANA_ADMIN_PASSWORD', 'admin')}".encode()
).decode()


def _req(method: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{GRAFANA}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {AUTH}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode() if exc.fp else ""
        raise RuntimeError(f"{method} {path} → HTTP {exc.code}: {body_text}") from exc


def _find_datasource(name: str) -> dict | None:
    for ds in _req("GET", "/api/datasources"):
        if ds["name"].lower() == name.lower():
            return ds
    return None


def _delete_datasource(name: str) -> None:
    ds = _find_datasource(name)
    if ds:
        _req("DELETE", f"/api/datasources/uid/{ds['uid']}")
        print(f"  removed cloud datasource: {name}")


def _ensure_datasource(payload: dict) -> str:
    name = payload["name"]
    existing = _find_datasource(name)
    if existing:
        uid = existing["uid"]
        _req("PUT", f"/api/datasources/uid/{uid}", {
            **payload,
            "id": existing["id"],
            "orgId": existing.get("orgId", 1),
            "uid": uid,
        })
        print(f"  updated datasource: {name} (uid={uid})")
        return uid
    result = _req("POST", "/api/datasources", payload)
    uid = result["datasource"]["uid"]
    print(f"  added datasource: {name} (uid={uid})")
    return uid


def _ensure_folder() -> str:
    try:
        folder = _req("GET", f"/api/folders/uid/{FOLDER_UID}")
        print(f"  folder exists: {folder['title']}")
        return FOLDER_UID
    except RuntimeError:
        pass
    try:
        folder = _req("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})
        print(f"  created folder: {FOLDER_TITLE}")
        return folder["uid"]
    except RuntimeError:
        for folder in _req("GET", "/api/folders"):
            if folder.get("uid") == FOLDER_UID or folder.get("title") == FOLDER_TITLE:
                print(f"  folder exists: {folder['title']}")
                return folder["uid"]
        raise


def _ds_ref(uid: str, ds_type: str) -> dict[str, str]:
    return {"type": ds_type, "uid": uid}


def _strip_export_metadata(dash: dict[str, Any]) -> None:
    dash.pop("__inputs", None)
    dash.pop("__requires", None)
    dash.pop("__elements", None)
    dash.pop("id", None)


def _patch_loki_expr(expr: str) -> str:
    """Normalize LogQL for native Loki OTLP ingestion (single JSON parse on log line)."""
    expr = expr.replace(
        '| json | line_format "{{.body}}" | json |',
        '| json |',
    )
    # Fix broken double-brace LogQL from older dashboard exports.
    expr = expr.replace("{{service_name=~", "{service_name=~")
    expr = expr.replace('budget_exhausted="True"', 'budget_exhausted="true"')
    # clamp_min is Prometheus-only; rewrite common Loki ratio pattern.
    if "clamp_min(" in expr:
        expr = (
            expr.replace("/ clamp_min(sum(count_over_time(", "/ (sum(count_over_time(")
            .replace("[1h])),1) * 100", "[1h])) or on() vector(1)) * 100")
            .replace("[24h])),1) * 100", "[24h])) or on() vector(1)) * 100")
            .replace("[5m])),1) * 100", "[5m])) or on() vector(1)) * 100")
        )
    return expr


def _patch_prom_expr(expr: str) -> str:
    """Rename metrics that differ between dashboard JSON and OTel export names."""
    replacements = (
        ("ai_gateway_request_duration_bucket", "ai_gateway_request_duration_milliseconds_bucket"),
        ("ai_gateway_request_duration_sum", "ai_gateway_request_duration_milliseconds_sum"),
        ("ai_gateway_request_duration_count", "ai_gateway_request_duration_milliseconds_count"),
        ("ai_gateway_request_cost_total", "ai_gateway_request_cost_USD_total"),
        ("otelcol_processor_dropped_spans_total", "otelcol_exporter_send_failed_spans"),
        ("otelcol_processor_dropped_log_records_total", "otelcol_exporter_send_failed_log_records"),
    )
    for old, new in replacements:
        expr = expr.replace(old, new)
    return expr


def _patch_queries(obj: Any) -> None:
    if isinstance(obj, dict):
        if isinstance(obj.get("expr"), str):
            expr = obj["expr"]
            if "service_name" in expr or "event_type" in expr or "prompt_log" in expr:
                expr = _patch_loki_expr(expr)
            else:
                expr = _patch_prom_expr(expr)
            obj["expr"] = expr
        for v in obj.values():
            _patch_queries(v)
    elif isinstance(obj, list):
        for item in obj:
            _patch_queries(item)


def _patch_datasource_refs(
    obj: Any, prom_uid: str, loki_uid: str, tempo_uid: str,
) -> None:
    """Resolve ${DS_*} placeholders and baked provisioning UIDs to concrete datasource UIDs."""
    mapping = {
        "${DS_PROMETHEUS}": (prom_uid, "prometheus"),
        "${DS_LOKI}": (loki_uid, "loki"),
        "${DS_TEMPO}": (tempo_uid, "tempo"),
        # dashboards/generate_dashboards.py bakes these for Docker provisioning;
        # Homebrew Grafana assigns its own UIDs unless recreated.
        "prometheus-ds": (prom_uid, "prometheus"),
        "loki-ds": (loki_uid, "loki"),
        "tempo-ds": (tempo_uid, "tempo"),
    }
    if isinstance(obj, dict):
        ds = obj.get("datasource")
        if isinstance(ds, dict):
            uid = ds.get("uid", "")
            if uid in mapping:
                concrete_uid, ds_type = mapping[uid]
                obj["datasource"] = _ds_ref(concrete_uid, ds_type)
        for v in obj.values():
            _patch_datasource_refs(v, prom_uid, loki_uid, tempo_uid)
    elif isinstance(obj, list):
        for item in obj:
            _patch_datasource_refs(item, prom_uid, loki_uid, tempo_uid)


def _patch_logs_panels(obj: Any) -> None:
    """Ensure logs panels use range queries with a sane line limit."""
    if isinstance(obj, dict):
        if obj.get("type") == "logs":
            for target in obj.get("targets", []):
                if isinstance(target, dict):
                    target.setdefault("queryType", "range")
                    target.setdefault("maxLines", 500)
                    target.setdefault("legendFormat", "")
        for v in obj.values():
            _patch_logs_panels(v)
    elif isinstance(obj, list):
        for item in obj:
            _patch_logs_panels(item)


def _fix_grafana11_color_modes(obj: Any) -> None:
    """Grafana 11 crashes when fieldConfig.color.mode is 'background' (use 'thresholds')."""
    if isinstance(obj, dict):
        color = obj.get("color")
        if isinstance(color, dict) and color.get("mode") == "background":
            color["mode"] = "thresholds"
        for v in obj.values():
            _fix_grafana11_color_modes(v)
    elif isinstance(obj, list):
        for item in obj:
            _fix_grafana11_color_modes(item)


def _patch_prom_stat_panels(obj: Any) -> None:
    """Prometheus stat/gauge/bar panels need range queries for rate/recording-rule metrics."""
    if isinstance(obj, dict):
        panel_type = obj.get("type", "")
        ds = obj.get("datasource")
        is_prom = isinstance(ds, dict) and ds.get("type") == "prometheus"
        if is_prom and panel_type in ("stat", "gauge", "barchart", "piechart", "bargauge") and isinstance(obj.get("targets"), list):
            for target in obj["targets"]:
                if isinstance(target, dict) and target.get("expr"):
                    target["range"] = True
                    target["instant"] = False
        for v in obj.values():
            _patch_prom_stat_panels(v)
    elif isinstance(obj, list):
        for item in obj:
            _patch_prom_stat_panels(item)


def _patch_loki_metric_panels(obj: Any) -> None:
    """Grafana Loki stat/gauge panels need queryType=instant; timeseries need range."""
    if isinstance(obj, dict):
        panel_type = obj.get("type", "")
        ds = obj.get("datasource")
        is_loki = isinstance(ds, dict) and ds.get("type") == "loki"
        targets = obj.get("targets")
        if is_loki and isinstance(targets, list):
            if panel_type in ("stat", "gauge", "bargauge", "piechart"):
                for target in targets:
                    if isinstance(target, dict):
                        target["instant"] = True
                        target["queryType"] = "instant"
            elif panel_type == "timeseries":
                for target in targets:
                    if isinstance(target, dict):
                        target.pop("instant", None)
                        target["queryType"] = "range"
        for v in obj.values():
            _patch_loki_metric_panels(v)
    elif isinstance(obj, list):
        for item in obj:
            _patch_loki_metric_panels(item)


def _fix_panel_refids(obj: Any) -> None:
    """Grafana requires unique refIds per target in a panel."""
    if isinstance(obj, dict):
        targets = obj.get("targets")
        if isinstance(targets, list) and len(targets) > 1:
            refs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            for i, target in enumerate(targets):
                if isinstance(target, dict):
                    target["refId"] = refs[i % len(refs)]
        for v in obj.values():
            _fix_panel_refids(v)
    elif isinstance(obj, list):
        for item in obj:
            _fix_panel_refids(item)


def _patch_modern_dashboard(
    dash: dict[str, Any], prom_uid: str, loki_uid: str, tempo_uid: str,
) -> None:
    ds_current = {
        "DS_PROMETHEUS": ("Prometheus", prom_uid),
        "DS_LOKI": ("Loki", loki_uid),
        "DS_TEMPO": ("Tempo", tempo_uid),
    }
    for var in dash.get("templating", {}).get("list", []):
        name = var.get("name", "")
        if name in ds_current:
            text, uid = ds_current[name]
            var["current"] = {"selected": True, "text": text, "value": uid}
            var["hide"] = 2  # hide — panels use pinned datasource UIDs locally
        elif name == "environment":
            env = os.getenv("ENVIRONMENT", "dev")
            var["current"] = {"selected": True, "text": env, "value": env}
        elif var.get("name") in (
            "model", "department", "region", "provider", "operation", "status", "data_class",
        ) and var.get("includeAll"):
            var["current"] = {"selected": True, "text": "All", "value": ".*"}
        # Grafana 10+ Prometheus variables expect a structured query object.
        if var.get("type") == "query" and isinstance(var.get("query"), str):
            q = var["query"]
            var["definition"] = q
            var["query"] = {
                "qryType": 1,
                "query": q,
                "refId": "PrometheusVariableQueryEditor-VariableQuery",
            }

    for ann in dash.get("annotations", {}).get("list", []):
        if ann.get("type") == "alert":
            ann["datasource"] = _ds_ref(prom_uid, "prometheus")
            ann["enable"] = False

    _patch_queries(dash.get("panels", []))
    _patch_logs_panels(dash.get("panels", []))
    _patch_loki_metric_panels(dash.get("panels", []))
    _patch_prom_stat_panels(dash.get("panels", []))
    _fix_grafana11_color_modes(dash)
    _fix_panel_refids(dash.get("panels", []))
    _patch_datasource_refs(dash, prom_uid, loki_uid, tempo_uid)


def _patch_legacy_azure_panel(panel: dict[str, Any], loki_uid: str) -> None:
    """Convert Azure Log Analytics panels to Loki for local dev."""
    title = panel.get("title", "")
    loki_ds = _ds_ref(loki_uid, "loki")
    panel["datasource"] = loki_ds
    panel.pop("pluginVersion", None)

    if title == "Log Events per Minute":
        panel["type"] = "timeseries"
        panel["targets"] = [{
            "datasource": loki_ds,
            "expr": 'sum(count_over_time({service_name=~".+"} | json | event_type="telemetry_event" [1m]))',
            "legendFormat": "events/min",
            "refId": "A",
        }]
    elif title == "Recent ERROR Logs":
        panel["type"] = "logs"
        panel["targets"] = [{
            "datasource": loki_ds,
            "expr": '{service_name=~".+"} | json | level="ERROR"',
            "refId": "A",
        }]
    elif title == "Events by Model (from Logs)":
        panel["type"] = "timeseries"
        panel["targets"] = [{
            "datasource": loki_ds,
            "expr": 'sum by (model_name) (count_over_time({service_name=~".+"} | json | event_type="telemetry_event" [5m]))',
            "legendFormat": "{{model_name}}",
            "refId": "A",
        }]
    else:
        for target in panel.get("targets", []):
            if isinstance(target, dict):
                target.pop("azureLogAnalytics", None)
                target.pop("queryType", None)


def _patch_legacy_dashboard(dash: dict[str, Any], prom_uid: str, loki_uid: str, tempo_uid: str) -> None:
    """Patch grafana_dashboard.json (legacy POC dashboard)."""
    for var in dash.get("templating", {}).get("list", []):
        if var.get("name") == "datasource":
            var["current"] = {"selected": True, "text": "Prometheus", "value": prom_uid}
        elif var.get("name") == "azuremonitor":
            var["hide"] = 2  # hide — Azure Monitor not available locally

    def _fix_legacy_expr(obj: Any) -> None:
        if isinstance(obj, dict):
            expr = obj.get("expr")
            if isinstance(expr, str):
                obj["expr"] = expr.replace("client_name", "tenant_id").replace("{{client_name}}", "{{tenant_id}}")
            for v in obj.values():
                _fix_legacy_expr(v)
        elif isinstance(obj, list):
            for item in obj:
                _fix_legacy_expr(item)

    _fix_legacy_expr(dash)

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("type") == "row":
                for child in obj.get("panels", []):
                    walk(child)
            ds = obj.get("datasource")
            if isinstance(ds, dict):
                if ds.get("uid") == "${datasource}":
                    obj["datasource"] = _ds_ref(prom_uid, "prometheus")
                elif ds.get("type") == "grafana-azure-monitor-datasource":
                    _patch_legacy_azure_panel(obj, loki_uid)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(dash.get("panels", []))
    _patch_queries(dash.get("panels", []))
    _patch_loki_metric_panels(dash.get("panels", []))
    _patch_prom_stat_panels(dash.get("panels", []))
    _fix_grafana11_color_modes(dash)
    _fix_panel_refids(dash.get("panels", []))
    _patch_datasource_refs(dash, prom_uid, loki_uid, tempo_uid)


def _import_dashboard(dash: dict[str, Any], folder_uid: str) -> str:
    result = _req("POST", "/api/dashboards/db", {
        "dashboard": dash,
        "overwrite": True,
        "folderUid": folder_uid,
        "message": "import all dashboards",
    })
    return result.get("url", "ok")


def _dashboard_files() -> list[Path]:
    numbered = sorted(DASHBOARDS_DIR.glob("0*.json"))
    legacy = DASHBOARDS_DIR / "grafana_dashboard.json"
    files = numbered + ([legacy] if legacy.exists() else [])
    return files


def _set_light_theme() -> None:
    """Default Grafana UI to light theme (canvas + panel backgrounds)."""
    for path, label in (("/api/org/preferences", "org"), ("/api/user/preferences", "user")):
        try:
            _req("PUT", path, {"theme": "light"})
            print(f"  set {label} theme: light")
        except RuntimeError as exc:
            print(f"  warning: could not set {label} theme ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Grafana datasources and dashboards")
    parser.add_argument(
        "--dashboards-only",
        action="store_true",
        help="Skip datasource setup; re-import dashboards with pinned UIDs (Azure after fix-grafana-datasources.sh)",
    )
    args = parser.parse_args()

    _set_light_theme()

    if args.dashboards_only:
        prom_uid = _find_datasource("Prometheus")
        loki_uid = _find_datasource("Loki")
        tempo_uid = _find_datasource("Tempo")
        if not prom_uid or not loki_uid or not tempo_uid:
            print("ERROR: Prometheus, Loki, and Tempo datasources must exist before --dashboards-only", file=sys.stderr)
            sys.exit(1)
        prom_uid = prom_uid["uid"]
        loki_uid = loki_uid["uid"]
        tempo_uid = tempo_uid["uid"]
        print(f"  using datasources: prom={prom_uid} loki={loki_uid} tempo={tempo_uid}")
    else:
        for name in ("azure-managed-prometheus", "Azure Monitor"):
            _delete_datasource(name)

        prom_uid = _ensure_datasource({
            "name": "Prometheus",
            "type": "prometheus",
            "access": "proxy",
            "url": os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
            "uid": "prometheus-ds",
            "isDefault": True,
            "jsonData": {
                "timeInterval": "10s",
                "tlsSkipVerify": True,
            },
        })
        loki_uid = _ensure_datasource({
            "name": "Loki",
            "type": "loki",
            "access": "proxy",
            "url": os.getenv("LOKI_URL", "http://localhost:3100"),
            "uid": "loki-ds",
            "jsonData": {
                "tlsSkipVerify": True,
                "derivedFields": [{
                    "datasourceUid": "tempo-ds",
                    "matcherRegex": '"trace_id":"([a-f0-9]{32})"',
                    "name": "trace_id",
                    "url": "$${__value.raw}",
                }],
            },
        })
        tempo_uid = _ensure_datasource({
            "name": "Tempo",
            "type": "tempo",
            "access": "proxy",
            "url": os.getenv("TEMPO_URL", "http://localhost:3200"),
            "uid": "tempo-ds",
            "jsonData": {
                "tlsSkipVerify": True,
                "tracesToLogsV2": {"datasourceUid": loki_uid},
                "tracesToMetrics": {"datasourceUid": prom_uid},
                "serviceMap": {"datasourceUid": prom_uid},
            },
        })

    folder_uid = _ensure_folder()

    print(f"  importing {len(_dashboard_files())} dashboards into '{FOLDER_TITLE}'...")
    for path in _dashboard_files():
        dash = json.loads(path.read_text(encoding="utf-8"))
        _strip_export_metadata(dash)
        if path.name == "grafana_dashboard.json":
            _patch_legacy_dashboard(dash, prom_uid, loki_uid, tempo_uid)
        else:
            _patch_modern_dashboard(dash, prom_uid, loki_uid, tempo_uid)
        url = _import_dashboard(dash, folder_uid)
        print(f"  OK {dash.get('title', path.name)} -> {url}")


if __name__ == "__main__":
    main()
