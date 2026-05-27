"""Grafana dashboard generator — produces 7 dashboard JSON files.

Run:
    python3 dashboards/generate_dashboards.py

Outputs:
    dashboards/01-executive-overview.json
    dashboards/02-traffic-analytics.json
    dashboards/03-latency-performance.json
    dashboards/04-token-cost.json
    dashboards/05-model-quality.json
    dashboards/06-safety-pii.json
    dashboards/07-infra-runner.json
"""
from __future__ import annotations

import json
import os
from typing import Any

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Datasource references (Grafana provisioned datasource UIDs)
# ---------------------------------------------------------------------------
DS_PROMETHEUS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
DS_LOKI       = {"type": "loki",       "uid": "${DS_LOKI}"}
DS_TEMPO      = {"type": "tempo",      "uid": "${DS_TEMPO}"}

# ---------------------------------------------------------------------------
# Common template variables (shared by every dashboard)
# ---------------------------------------------------------------------------

def _ds_var(name: str, ds_type: str, label: str) -> dict:
    return {
        "type": "datasource", "name": name, "label": label,
        "pluginId": ds_type, "multi": False, "includeAll": False,
        "hide": 0, "refresh": 1, "current": {},
    }

def _query_var(name: str, label: str, datasource: dict, query: str,
               multi: bool = True, include_all: bool = True) -> dict:
    return {
        "type": "query", "name": name, "label": label,
        "datasource": datasource,
        "query": query,
        "multi": multi, "includeAll": include_all,
        "allValue": ".*", "hide": 0, "refresh": 2, "current": {},
        "sort": 1,
    }

COMMON_VARS: list[dict] = [
    _ds_var("DS_PROMETHEUS", "prometheus", "Prometheus"),
    _ds_var("DS_LOKI",       "loki",       "Loki"),
    _ds_var("DS_TEMPO",      "tempo",      "Tempo"),
    _query_var(
        "tenant", "Tenant",
        DS_PROMETHEUS,
        'label_values(ai_gateway_request_count_total, tenant_id)',
        multi=True, include_all=True,
    ),
    _query_var(
        "model", "Model",
        DS_PROMETHEUS,
        'label_values(ai_gateway_request_count_total, model_name)',
        multi=True, include_all=True,
    ),
    _query_var(
        "environment", "Environment",
        DS_PROMETHEUS,
        'label_values(ai_gateway_request_count_total, environment)',
        multi=False, include_all=False,
    ),
]

# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

_id_counter = 0

def _next_id() -> int:
    global _id_counter
    _id_counter += 1
    return _id_counter


def _grid(x: int, y: int, w: int, h: int) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def stat_panel(
    title: str,
    expr: str,
    unit: str = "short",
    color_mode: str = "background",
    thresholds: list[dict] | None = None,
    grid: dict | None = None,
    datasource: dict | None = None,
    mappings: list | None = None,
    decimals: int = 2,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    th = thresholds or [
        {"color": "green",  "value": None},
        {"color": "yellow", "value": 80},
        {"color": "red",    "value": 95},
    ]
    return {
        "id": _next_id(), "type": "stat", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit, "decimals": decimals,
                "color": {"mode": color_mode},
                "thresholds": {"mode": "absolute", "steps": th},
                "mappings": mappings or [],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": color_mode, "graphMode": "area",
            "justifyMode": "center", "textMode": "auto",
        },
        "gridPos": grid or _grid(0, 0, 4, 3),
        "targets": [{"datasource": ds, "expr": expr, "instant": True, "refId": "A"}],
    }


def timeseries_panel(
    title: str,
    targets: list[dict],
    unit: str = "short",
    grid: dict | None = None,
    datasource: dict | None = None,
    stacking: str = "none",
    fill_opacity: int = 5,
    legend_placement: str = "bottom",
) -> dict:
    ds = datasource or DS_PROMETHEUS
    return {
        "id": _next_id(), "type": "timeseries", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "lineWidth": 1, "fillOpacity": fill_opacity,
                    "gradientMode": "none",
                    "stacking": {"mode": stacking},
                    "showPoints": "never",
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": legend_placement, "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": targets,
    }


def _prom_target(expr: str, legend: str, ref: str = "A") -> dict:
    return {
        "datasource": DS_PROMETHEUS, "expr": expr,
        "legendFormat": legend, "refId": ref,
    }


def _loki_target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {
        "datasource": DS_LOKI, "expr": expr,
        "legendFormat": legend, "refId": ref,
    }


def gauge_panel(
    title: str, expr: str, unit: str = "percent",
    min_val: float = 0, max_val: float = 100,
    thresholds: list[dict] | None = None,
    grid: dict | None = None,
) -> dict:
    th = thresholds or [
        {"color": "red",    "value": None},
        {"color": "yellow", "value": 25},
        {"color": "green",  "value": 50},
    ]
    return {
        "id": _next_id(), "type": "gauge", "title": title,
        "datasource": DS_PROMETHEUS,
        "fieldConfig": {
            "defaults": {
                "unit": unit, "min": min_val, "max": max_val,
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": th},
            },
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "showThresholdLabels": True},
        "gridPos": grid or _grid(0, 0, 6, 6),
        "targets": [{"datasource": DS_PROMETHEUS, "expr": expr, "instant": True, "refId": "A"}],
    }


def barchart_panel(
    title: str, targets: list[dict], unit: str = "short",
    grid: dict | None = None, datasource: dict | None = None,
    orientation: str = "auto",
) -> dict:
    ds = datasource or DS_PROMETHEUS
    return {
        "id": _next_id(), "type": "barchart", "title": title,
        "datasource": ds,
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {
            "orientation": orientation,
            "xTickLabelRotation": -45,
            "barRadius": 0.03,
            "groupWidth": 0.7,
            "barWidth": 0.97,
            "stacking": "none",
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": targets,
    }


def piechart_panel(
    title: str, targets: list[dict],
    grid: dict | None = None, datasource: dict | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    return {
        "id": _next_id(), "type": "piechart", "title": title,
        "datasource": ds,
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
        "options": {
            "pieType": "pie",
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                       "values": ["value", "percent"]},
            "tooltip": {"mode": "single"},
        },
        "gridPos": grid or _grid(0, 0, 8, 8),
        "targets": targets,
    }


def bargauge_panel(
    title: str, expr: str, unit: str = "percent",
    grid: dict | None = None,
    thresholds: list[dict] | None = None,
    datasource: dict | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    th = thresholds or [
        {"color": "green",  "value": None},
        {"color": "yellow", "value": 75},
        {"color": "red",    "value": 90},
    ]
    return {
        "id": _next_id(), "type": "bargauge", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": th},
            },
            "overrides": [],
        },
        "options": {
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "displayMode": "lcd",
            "showUnfilled": True,
        },
        "gridPos": grid or _grid(0, 0, 12, 6),
        "targets": [{"datasource": ds, "expr": expr, "instant": True,
                     "legendFormat": "{{tenant_id}}", "refId": "A"}],
    }


def heatmap_panel(
    title: str, expr: str, unit: str = "ms",
    grid: dict | None = None,
) -> dict:
    return {
        "id": _next_id(), "type": "heatmap", "title": title,
        "datasource": DS_PROMETHEUS,
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {
            "calculate": False,
            "yAxis": {"unit": unit},
            "color": {"scheme": "Oranges", "mode": "scheme"},
            "tooltip": {"mode": "single"},
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": [{"datasource": DS_PROMETHEUS, "expr": expr,
                     "format": "heatmap", "legendFormat": "{{le}}", "refId": "A"}],
    }


def table_panel(
    title: str, targets: list[dict],
    grid: dict | None = None,
    datasource: dict | None = None,
) -> dict:
    ds = datasource or DS_LOKI
    return {
        "id": _next_id(), "type": "table", "title": title,
        "datasource": ds,
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
        "options": {
            "sortBy": [{"displayName": "Time", "desc": True}],
            "footer": {"show": False},
        },
        "gridPos": grid or _grid(0, 0, 24, 8),
        "targets": targets,
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {"__name__": True}}},
        ],
    }


def logs_panel(
    title: str, expr: str,
    grid: dict | None = None,
    datasource: dict | None = None,
) -> dict:
    ds = datasource or DS_LOKI
    return {
        "id": _next_id(), "type": "logs", "title": title,
        "datasource": ds,
        "options": {
            "dedupStrategy": "none",
            "enableLogDetails": True,
            "prettifyLogMessage": True,
            "showTime": True,
            "sortOrder": "Descending",
            "wrapLogMessage": False,
        },
        "gridPos": grid or _grid(0, 0, 24, 10),
        "targets": [{"datasource": ds, "expr": expr, "refId": "A"}],
    }


def alertlist_panel(title: str, grid: dict | None = None) -> dict:
    return {
        "id": _next_id(), "type": "alertlist", "title": title,
        "options": {
            "alertInstanceLabelFilter": "",
            "alertName": "",
            "dashboardAlerts": False,
            "groupMode": "default",
            "maxItems": 20,
            "sortOrder": 1,
            "stateFilter": {
                "error": True, "firing": True, "noData": False,
                "normal": False, "pending": True,
            },
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": [],
    }


def row_panel(title: str, y: int, collapsed: bool = False) -> dict:
    return {
        "id": _next_id(), "type": "row", "title": title,
        "collapsed": collapsed, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def text_panel(title: str, content: str, grid: dict | None = None) -> dict:
    return {
        "id": _next_id(), "type": "text", "title": title,
        "options": {"content": content, "mode": "markdown"},
        "gridPos": grid or _grid(0, 0, 24, 2),
        "targets": [],
    }


# ---------------------------------------------------------------------------
# Dashboard skeleton
# ---------------------------------------------------------------------------

def dashboard(
    uid: str, title: str, description: str, tags: list[str],
    panels: list[dict],
    refresh: str = "30s",
) -> dict:
    global _id_counter
    _id_counter = 0   # reset per dashboard so IDs start at 1
    # Re-assign sequential IDs
    for i, p in enumerate(panels, 1):
        p["id"] = i
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": tags,
        "schemaVersion": 39,
        "version": 1,
        "refresh": refresh,
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "templating": {"list": COMMON_VARS},
        "annotations": {
            "list": [
                {
                    "name": "Alerts",
                    "type": "alert",
                    "datasource": DS_PROMETHEUS,
                    "enable": True,
                    "hide": False,
                    "iconColor": "red",
                }
            ]
        },
        "panels": panels,
        "links": [
            {
                "title": "All AI Telemetry Dashboards",
                "type": "dashboards",
                "tags": ["ai-telemetry"],
                "targetBlank": False,
                "icon": "external link",
            }
        ],
    }


def _save(name: str, d: dict) -> None:
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    print(f"  wrote {name}  ({len(d['panels'])} panels)")


# ===========================================================================
# D A S H B O A R D   1 — Executive Overview / Golden Signals
# ===========================================================================

def build_d1() -> dict:
    global _id_counter; _id_counter = 0

    # Row 0 — SLO health
    panels = [
        stat_panel(
            "Availability (6h SLI)", "ai_gateway:sli:availability:6h * 100",
            unit="percent", decimals=3,
            thresholds=[{"color":"red","value":None},{"color":"yellow","value":99},{"color":"green","value":99.5}],
            grid=_grid(0, 0, 4, 4),
        ),
        gauge_panel(
            "Error Budget Remaining",
            "clamp_min(ai_gateway:slo:error_budget_remaining * 100, 0)",
            unit="percent", min_val=0, max_val=100,
            thresholds=[{"color":"red","value":None},{"color":"yellow","value":25},{"color":"green","value":60}],
            grid=_grid(4, 0, 4, 4),
        ),
        stat_panel(
            "Requests / min",
            'sum(rate(ai_gateway_request_count_total{environment=~"$environment"}[1m])) * 60',
            unit="reqps", decimals=1,
            thresholds=[{"color":"blue","value":None}],
            grid=_grid(8, 0, 4, 4),
        ),
        stat_panel(
            "Error Rate",
            'sum(rate(ai_gateway_exception_count_total{environment=~"$environment"}[5m])) / clamp_min(sum(rate(ai_gateway_request_count_total{environment=~"$environment"}[5m])),1e-9) * 100',
            unit="percent", decimals=2,
            thresholds=[{"color":"green","value":None},{"color":"yellow","value":1},{"color":"red","value":5}],
            grid=_grid(12, 0, 4, 4),
        ),
        stat_panel(
            "p99 Latency (5m)",
            "ai_gateway:sli:latency_p99_ms:5m",
            unit="ms", decimals=0,
            thresholds=[{"color":"green","value":None},{"color":"yellow","value":2000},{"color":"red","value":5000}],
            grid=_grid(16, 0, 4, 4),
        ),
        stat_panel(
            "Total Cost Today (USD)",
            'sum(increase(ai_gateway_request_cost_total{environment=~"$environment"}[24h]))',
            unit="currencyUSD", decimals=2,
            thresholds=[{"color":"blue","value":None}],
            grid=_grid(20, 0, 4, 4),
        ),

        # Row 1 — timeseries
        row_panel("Request & Error Trends", y=4),
        timeseries_panel(
            "Request Rate by Model",
            [_prom_target(
                'sum by (model_name) (rate(ai_gateway_request_count_total{environment=~"$environment",tenant_id=~"$tenant"}[2m]))',
                "{{model_name}}", "A",
            )],
            unit="reqps", grid=_grid(0, 5, 12, 8),
        ),
        timeseries_panel(
            "Error Rate Over Time",
            [_prom_target(
                'sum(rate(ai_gateway_exception_count_total{environment=~"$environment",tenant_id=~"$tenant"}[5m])) / clamp_min(sum(rate(ai_gateway_request_count_total{environment=~"$environment",tenant_id=~"$tenant"}[5m])),1e-9) * 100',
                "Error %", "A",
            )],
            unit="percent", grid=_grid(12, 5, 12, 8),
        ),

        # Row 2 — SLO burn
        row_panel("SLO Burn Rate", y=13),
        timeseries_panel(
            "SLO Error Budget Burn Rate",
            [
                _prom_target("(1 - ai_gateway:sli:availability:1h) / 0.005",  "1h burn rate",  "A"),
                _prom_target("(1 - ai_gateway:sli:availability:6h) / 0.005",  "6h burn rate",  "B"),
                _prom_target("(1 - ai_gateway:sli:availability:30m) / 0.005", "30m burn rate", "C"),
            ],
            unit="short", grid=_grid(0, 14, 16, 8),
        ),
        alertlist_panel("Firing Alerts", grid=_grid(16, 14, 8, 8)),

        # Row 3 — by tenant
        row_panel("Tenant Breakdown", y=22),
        barchart_panel(
            "Requests by Tenant (last 1h)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (tenant_id) (increase(ai_gateway_request_count_total{environment=~"$environment"}[1h])))', "legendFormat": "{{tenant_id}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(0, 23, 12, 8),
        ),
        barchart_panel(
            "Cost by Tenant (last 1h USD)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (tenant_id) (increase(ai_gateway_request_cost_total{environment=~"$environment"}[1h])))', "legendFormat": "{{tenant_id}}", "refId": "A", "instant": True}],
            unit="currencyUSD", grid=_grid(12, 23, 12, 8),
        ),
    ]

    return dashboard(
        uid="ai-telemetry-executive",
        title="1 — AI Gateway: Executive Overview",
        description="SLO availability, error budget, cost and traffic at a glance.",
        tags=["ai-telemetry", "slo", "executive"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   2 — Traffic & Request Analytics
# ===========================================================================

def build_d2() -> dict:
    global _id_counter; _id_counter = 0

    panels = [
        row_panel("Traffic Volume", y=0),
        timeseries_panel(
            "Requests / min by Model",
            [_prom_target('sum by (model_name) (rate(ai_gateway_request_count_total{tenant_id=~"$tenant"}[2m])) * 60', "{{model_name}}")],
            unit="r/min", grid=_grid(0, 1, 12, 8),
        ),
        timeseries_panel(
            "Requests / min by Tenant",
            [_prom_target('sum by (tenant_id) (rate(ai_gateway_request_count_total[2m])) * 60', "{{tenant_id}}")],
            unit="r/min", grid=_grid(12, 1, 12, 8),
        ),
        timeseries_panel(
            "Requests / min by Operation",
            [_prom_target('sum by (operation_name) (rate(ai_gateway_request_count_total{tenant_id=~"$tenant"}[2m])) * 60', "{{operation_name}}")],
            unit="r/min", grid=_grid(0, 9, 12, 8),
        ),
        piechart_panel(
            "Model Distribution (last 1h)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (model_name) (increase(ai_gateway_request_count_total[1h])))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
            grid=_grid(12, 9, 6, 8),
        ),
        piechart_panel(
            "Model Provider Distribution",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (model_provider) (increase(ai_gateway_request_count_total[1h])))', "legendFormat": "{{model_provider}}", "refId": "A", "instant": True}],
            grid=_grid(18, 9, 6, 8),
        ),

        row_panel("Errors & Retries", y=17),
        timeseries_panel(
            "Error Rate by Type",
            [_prom_target('sum by (error_type) (rate(ai_gateway_exception_count_total{tenant_id=~"$tenant"}[5m]))', "{{error_type}}")],
            unit="reqps", grid=_grid(0, 18, 12, 8),
        ),
        barchart_panel(
            "Errors by HTTP Status Code (last 1h)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (http_status) (increase(ai_gateway_exception_count_total{tenant_id=~"$tenant"}[1h])))', "legendFormat": "{{http_status}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(12, 18, 8, 8),
        ),
        piechart_panel(
            "Error Category Mix",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (error_category) (increase(ai_gateway_exception_count_total{tenant_id=~"$tenant"}[1h])))', "legendFormat": "{{error_category}}", "refId": "A", "instant": True}],
            grid=_grid(20, 18, 4, 8),
        ),

        row_panel("SLA Compliance", y=26),
        timeseries_panel(
            "SLA Breach Rate by Tenant",
            [_prom_target('sum by (tenant_id) (rate(ai_gateway_request_count_total{status="error",tenant_id=~"$tenant"}[5m])) / clamp_min(sum by (tenant_id) (rate(ai_gateway_request_count_total{tenant_id=~"$tenant"}[5m])),1e-9) * 100', "{{tenant_id}}")],
            unit="percent", grid=_grid(0, 27, 12, 8),
        ),
        barchart_panel(
            "Total Requests by Routing Reason (last 1h)",
            [{"datasource": DS_LOKI, "expr": 'sum by (routing_reason) (count_over_time({service_name=~".+"} | json | event_type = "telemetry_event" [1h]))', "legendFormat": "{{routing_reason}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(12, 27, 12, 8),
            datasource=DS_LOKI,
        ),

        row_panel("Live Request Log", y=35),
        logs_panel(
            "Live Telemetry Events",
            '{service_name=~".+"} | json | event_type = "telemetry_event" | tenant_id =~ "$tenant"',
            grid=_grid(0, 36, 24, 10),
        ),
    ]

    return dashboard(
        uid="ai-telemetry-traffic",
        title="2 — AI Gateway: Traffic & Request Analytics",
        description="Request volumes, model usage, error taxonomy, SLA compliance.",
        tags=["ai-telemetry", "traffic", "errors"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   3 — Latency & Performance
# ===========================================================================

def build_d3() -> dict:
    global _id_counter; _id_counter = 0

    panels = [
        row_panel("Latency Percentiles", y=0),
        timeseries_panel(
            "Request Latency — p50 / p95 / p99",
            [
                _prom_target('histogram_quantile(0.50, sum by (le) (rate(ai_gateway_request_duration_bucket{tenant_id=~"$tenant"}[5m])))', "p50", "A"),
                _prom_target('histogram_quantile(0.95, sum by (le) (rate(ai_gateway_request_duration_bucket{tenant_id=~"$tenant"}[5m])))', "p95", "B"),
                _prom_target('histogram_quantile(0.99, sum by (le) (rate(ai_gateway_request_duration_bucket{tenant_id=~"$tenant"}[5m])))', "p99", "C"),
            ],
            unit="ms", grid=_grid(0, 1, 16, 9),
        ),
        stat_panel("Current p99 (5m)", "ai_gateway:sli:latency_p99_ms:5m",
                   unit="ms", decimals=0,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":2000},{"color":"red","value":5000}],
                   grid=_grid(16, 1, 4, 4)),
        stat_panel("Current p95 (5m)",
                   'histogram_quantile(0.95, sum by (le) (rate(ai_gateway_request_duration_bucket[5m])))',
                   unit="ms", decimals=0,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":1500},{"color":"red","value":4000}],
                   grid=_grid(20, 1, 4, 4)),
        stat_panel("Current p50 (5m)",
                   'histogram_quantile(0.50, sum by (le) (rate(ai_gateway_request_duration_bucket[5m])))',
                   unit="ms", decimals=0,
                   thresholds=[{"color":"green","value":None}],
                   grid=_grid(16, 5, 4, 4)),
        stat_panel("Avg Latency (5m)",
                   'sum(rate(ai_gateway_request_duration_sum[5m])) / clamp_min(sum(rate(ai_gateway_request_duration_count[5m])),1e-9)',
                   unit="ms", decimals=0,
                   thresholds=[{"color":"green","value":None}],
                   grid=_grid(20, 5, 4, 4)),

        row_panel("Latency Breakdown", y=10),
        heatmap_panel(
            "Latency Heatmap",
            'sum by (le) (rate(ai_gateway_request_duration_bucket{tenant_id=~"$tenant"}[2m]))',
            unit="ms", grid=_grid(0, 11, 12, 9),
        ),
        timeseries_panel(
            "p95 Latency by Model",
            [_prom_target('histogram_quantile(0.95, sum by (le, model_name) (rate(ai_gateway_request_duration_bucket{tenant_id=~"$tenant"}[5m])))', "{{model_name}}")],
            unit="ms", grid=_grid(12, 11, 12, 9),
        ),
        timeseries_panel(
            "p95 Latency by SLA Tier",
            [_prom_target('histogram_quantile(0.95, sum by (le, environment) (rate(ai_gateway_request_duration_bucket[5m])))', "{{environment}}")],
            unit="ms", grid=_grid(0, 20, 12, 8),
        ),
        barchart_panel(
            "Avg Latency Phase Breakdown by Model",
            [
                {"datasource": DS_LOKI, "expr": 'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="telemetry_event" | unwrap queue_wait_ms [10m]))', "legendFormat": "queue_wait — {{model_name}}", "refId": "A"},
                {"datasource": DS_LOKI, "expr": 'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="telemetry_event" | unwrap model_inference_ms [10m]))', "legendFormat": "inference — {{model_name}}", "refId": "B"},
                {"datasource": DS_LOKI, "expr": 'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="telemetry_event" | unwrap stream_response_ms [10m]))', "legendFormat": "stream — {{model_name}}", "refId": "C"},
            ],
            unit="ms", grid=_grid(12, 20, 12, 8),
            datasource=DS_LOKI,
        ),

        row_panel("Streaming & Throughput", y=28),
        timeseries_panel(
            "Tokens / Second (Streaming Requests)",
            [_loki_target(
                'avg(avg_over_time({service_name=~".+"} | json | event_type="telemetry_event" | streaming="true" | unwrap tokens_per_second [5m]))',
                "avg tokens/s",
            )],
            unit="short", grid=_grid(0, 29, 12, 8), datasource=DS_LOKI,
        ),
        timeseries_panel(
            "First-Token Latency (Streaming, 5m avg)",
            [_loki_target(
                'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="telemetry_event" | streaming="true" | unwrap first_token_ms [5m]))',
                "{{model_name}}",
            )],
            unit="ms", grid=_grid(12, 29, 12, 8), datasource=DS_LOKI,
        ),

        row_panel("Slow-Request Traces (Exemplars → Tempo)", y=37),
        timeseries_panel(
            "p99 Latency with Trace Exemplars",
            [
                {
                    "datasource": DS_PROMETHEUS,
                    "expr": 'histogram_quantile(0.99, sum by (le) (rate(ai_gateway_request_duration_bucket[5m])))',
                    "legendFormat": "p99",
                    "exemplarTraceIdDestinations": [{"datasourceUid": "${DS_TEMPO}", "name": "trace_id"}],
                    "refId": "A",
                }
            ],
            unit="ms", grid=_grid(0, 38, 24, 9),
        ),
    ]

    return dashboard(
        uid="ai-telemetry-latency",
        title="3 — AI Gateway: Latency & Performance",
        description="End-to-end latency, latency phases, streaming throughput, exemplar trace links.",
        tags=["ai-telemetry", "latency", "performance"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   4 — Token & Cost Analytics
# ===========================================================================

def build_d4() -> dict:
    global _id_counter; _id_counter = 0

    panels = [
        row_panel("Cost Summary", y=0),
        stat_panel("Total Cost Today (USD)", 'sum(increase(ai_gateway_request_cost_total[24h]))', unit="currencyUSD", decimals=2, thresholds=[{"color":"blue","value":None}], grid=_grid(0, 1, 4, 4)),
        stat_panel("Cost Rate (USD/min)", 'sum(rate(ai_gateway_request_cost_total[5m])) * 60', unit="currencyUSD", decimals=4, thresholds=[{"color":"blue","value":None}], grid=_grid(4, 1, 4, 4)),
        stat_panel("Total Tokens Today", 'sum(increase(ai_gateway_request_token_total[24h]))', unit="short", decimals=0, thresholds=[{"color":"blue","value":None}], grid=_grid(8, 1, 4, 4)),
        stat_panel("Prompt Tokens Today", 'sum(increase(ai_gateway_request_token_total{token_type="prompt"}[24h]))', unit="short", decimals=0, thresholds=[{"color":"blue","value":None}], grid=_grid(12, 1, 4, 4)),
        stat_panel("Completion Tokens Today", 'sum(increase(ai_gateway_request_token_total{token_type="completion"}[24h]))', unit="short", decimals=0, thresholds=[{"color":"blue","value":None}], grid=_grid(16, 1, 4, 4)),
        stat_panel("Cache Read Tokens Today", 'sum(increase(ai_gateway_request_token_total{token_type="cache_read"}[24h]))', unit="short", decimals=0, thresholds=[{"color":"green","value":None}], grid=_grid(20, 1, 4, 4)),

        row_panel("Cost Over Time", y=5),
        timeseries_panel(
            "Cost Rate by Model (USD/min)",
            [_prom_target('sum by (model_name) (rate(ai_gateway_request_cost_total{tenant_id=~"$tenant"}[5m])) * 60', "{{model_name}}")],
            unit="currencyUSD", grid=_grid(0, 6, 12, 8),
        ),
        timeseries_panel(
            "Cost Rate by Tenant (USD/min)",
            [_prom_target('sum by (tenant_id) (rate(ai_gateway_request_cost_total[5m])) * 60', "{{tenant_id}}")],
            unit="currencyUSD", grid=_grid(12, 6, 12, 8),
        ),

        row_panel("Model Cost Breakdown", y=14),
        piechart_panel(
            "Cost Share by Model (last 1h)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (model_name) (increase(ai_gateway_request_cost_total[1h])))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
            grid=_grid(0, 15, 8, 8),
        ),
        barchart_panel(
            "Cost per Model (last 24h USD)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (model_name) (increase(ai_gateway_request_cost_total[24h])))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
            unit="currencyUSD", grid=_grid(8, 15, 8, 8),
        ),
        barchart_panel(
            "Cost per Tenant (last 24h USD)",
            [{"datasource": DS_PROMETHEUS, "expr": 'sort_desc(sum by (tenant_id) (increase(ai_gateway_request_cost_total[24h])))', "legendFormat": "{{tenant_id}}", "refId": "A", "instant": True}],
            unit="currencyUSD", grid=_grid(16, 15, 8, 8),
        ),

        row_panel("Budget Tracking", y=23),
        bargauge_panel(
            "Daily Budget Utilisation by Tenant",
            'sum by (tenant_id) (increase(ai_gateway_request_cost_total[24h]))',
            unit="currencyUSD",
            thresholds=[{"color":"green","value":None},{"color":"yellow","value":50},{"color":"red","value":80}],
            grid=_grid(0, 24, 12, 8),
        ),
        timeseries_panel(
            "Budget-Exhausted Events Over Time",
            [_loki_target(
                'sum(count_over_time({service_name=~".+"} | json | event_type="telemetry_event" | budget_exhausted="True" [2m]))',
                "budget exhausted events",
            )],
            unit="short", grid=_grid(12, 24, 12, 8), datasource=DS_LOKI,
        ),

        row_panel("Token Efficiency & Cache", y=32),
        timeseries_panel(
            "Token Consumption Rate by Type",
            [
                _prom_target('sum(rate(ai_gateway_request_token_total{token_type="prompt",tenant_id=~"$tenant"}[5m]))', "prompt"),
                _prom_target('sum(rate(ai_gateway_request_token_total{token_type="completion",tenant_id=~"$tenant"}[5m]))', "completion"),
                _prom_target('sum(rate(ai_gateway_request_token_total{token_type="cache_read",tenant_id=~"$tenant"}[5m]))', "cache_read"),
            ],
            unit="short", grid=_grid(0, 33, 12, 8), stacking="normal", fill_opacity=10,
        ),
        timeseries_panel(
            "Cache Hit Tokens vs Prompt Tokens (Cost Savings)",
            [
                _prom_target('sum(rate(ai_gateway_request_token_total{token_type="cache_read"}[5m]))', "cache_read (saved)"),
                _prom_target('sum(rate(ai_gateway_request_token_total{token_type="prompt"}[5m]))', "prompt (billed)"),
            ],
            unit="short", grid=_grid(12, 33, 12, 8),
        ),
    ]

    return dashboard(
        uid="ai-telemetry-cost",
        title="4 — AI Gateway: Token & Cost Analytics",
        description="Token consumption, cost by model and tenant, budget utilisation, cache savings.",
        tags=["ai-telemetry", "cost", "tokens", "budget"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   5 — Model Quality & Evaluation
# ===========================================================================

def build_d5() -> dict:
    global _id_counter; _id_counter = 0

    # Loki queries on eval_result event_type
    _base = '{service_name=~".+"} | json | event_type = "eval_result"'
    _tenant_filter = f'{_base} | tenant_id =~ "$tenant"'

    panels = [
        row_panel("Quality Score Summary", y=0),
        stat_panel("Avg Faithfulness", f'avg(avg_over_time({{{_tenant_filter}}} | unwrap faithfulness [1h]))',
                   unit="short", decimals=1,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":6},{"color":"green","value":8}],
                   grid=_grid(0, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Avg Relevance", f'avg(avg_over_time({{{_tenant_filter}}} | unwrap relevance [1h]))',
                   unit="short", decimals=1,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":6},{"color":"green","value":8}],
                   grid=_grid(4, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Avg Groundedness", f'avg(avg_over_time({{{_tenant_filter}}} | unwrap groundedness [1h]))',
                   unit="short", decimals=1,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":6},{"color":"green","value":8}],
                   grid=_grid(8, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Evaluation Coverage",
                   'sum(count_over_time({service_name=~".+"} | json | event_type="eval_result" [1h])) / clamp_min(sum(count_over_time({service_name=~".+"} | json | event_type="telemetry_event" [1h])),1) * 100',
                   unit="percent", decimals=2,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(12, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Evaluator Daily Tokens Used",
                   f'sum(sum_over_time({{{_base}}} | unwrap tokens_used [24h]))',
                   unit="short", decimals=0,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(16, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Evaluator Errors (24h)",
                   'sum(count_over_time({service_name=~".+"} | json | event_type="eval_result" | error != "" [24h]))',
                   unit="short", decimals=0,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":1},{"color":"red","value":10}],
                   grid=_grid(20, 1, 4, 4), datasource=DS_LOKI),

        row_panel("Quality Trends Over Time", y=5),
        timeseries_panel(
            "Quality Scores (1h rolling avg)",
            [
                _loki_target('avg(avg_over_time({service_name=~".+"} | json | event_type="eval_result" | tenant_id=~"$tenant" | unwrap faithfulness [1h]))', "faithfulness"),
                _loki_target('avg(avg_over_time({service_name=~".+"} | json | event_type="eval_result" | tenant_id=~"$tenant" | unwrap relevance [1h]))',    "relevance"),
                _loki_target('avg(avg_over_time({service_name=~".+"} | json | event_type="eval_result" | tenant_id=~"$tenant" | unwrap groundedness [1h]))', "groundedness"),
            ],
            unit="short", grid=_grid(0, 6, 24, 9), datasource=DS_LOKI,
        ),

        row_panel("Quality by Model", y=15),
        barchart_panel(
            "Avg Faithfulness by Model",
            [{"datasource": DS_LOKI, "expr": 'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="eval_result" | unwrap faithfulness [1h]))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(0, 16, 8, 8), datasource=DS_LOKI,
        ),
        barchart_panel(
            "Avg Relevance by Model",
            [{"datasource": DS_LOKI, "expr": 'avg by (model_name) (avg_over_time({service_name=~".+"} | json | event_type="eval_result" | unwrap relevance [1h]))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(8, 16, 8, 8), datasource=DS_LOKI,
        ),
        barchart_panel(
            "Avg Groundedness by Operation",
            [{"datasource": DS_LOKI, "expr": 'avg by (operation_name) (avg_over_time({service_name=~".+"} | json | event_type="eval_result" | unwrap groundedness [1h]))', "legendFormat": "{{operation_name}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(16, 16, 8, 8), datasource=DS_LOKI,
        ),

        row_panel("Low-Quality Events", y=24),
        logs_panel(
            "Low-Quality Responses (faithfulness < 5)",
            '{service_name=~".+"} | json | event_type = "eval_result" | faithfulness < 5 | tenant_id =~ "$tenant"',
            grid=_grid(0, 25, 24, 10), datasource=DS_LOKI,
        ),
        timeseries_panel(
            "Evaluator Error Rate",
            [_loki_target('sum(count_over_time({service_name=~".+"} | json | event_type="eval_result" | error != "" [5m]))', "errors/5m")],
            unit="short", grid=_grid(0, 35, 12, 8), datasource=DS_LOKI,
        ),
        timeseries_panel(
            "Evaluator Latency (ms)",
            [_loki_target('avg(avg_over_time({service_name=~".+"} | json | event_type="eval_result" | unwrap latency_ms [5m]))', "avg eval latency ms")],
            unit="ms", grid=_grid(12, 35, 12, 8), datasource=DS_LOKI,
        ),
    ]

    return dashboard(
        uid="ai-telemetry-quality",
        title="5 — AI Gateway: Model Quality & Evaluation",
        description="OpenAI-as-judge faithfulness, relevance, groundedness scores by model and operation.",
        tags=["ai-telemetry", "quality", "evaluation"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   6 — Safety & PII / Compliance
# ===========================================================================

def build_d6() -> dict:
    global _id_counter; _id_counter = 0

    _plog = '{service_name=~".+"} | json | event_type = "prompt_log_event"'
    _tele = '{service_name=~".+"} | json | event_type = "telemetry_event"'

    panels = [
        row_panel("PII Detection Summary", y=0),
        stat_panel("PII Detection Rate (24h)",
                   f'sum(count_over_time({{{_plog} | pii_detected="true" [24h]}})) / clamp_min(sum(count_over_time({{{_plog} [24h]}})),1) * 100',
                   unit="percent", decimals=1,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":5},{"color":"red","value":15}],
                   grid=_grid(0, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("PII Events Today",
                   f'sum(count_over_time({{{_plog} | pii_detected="true" [24h]}})) or vector(0)',
                   unit="short", decimals=0,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":10},{"color":"red","value":50}],
                   grid=_grid(4, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("PHI Requests Today",
                   f'sum(count_over_time({{{_tele} | data_classification="phi" [24h]}})) or vector(0)',
                   unit="short", decimals=0,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(8, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("PII Requests Today",
                   f'sum(count_over_time({{{_tele} | data_classification="pii" [24h]}})) or vector(0)',
                   unit="short", decimals=0,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(12, 1, 4, 4), datasource=DS_LOKI),
        stat_panel("Unique Prompt Hashes (24h)",
                   f'count(count_over_time({{{_plog} [24h]}} | label_format ph=prompt_hash))',
                   unit="short", decimals=0,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(16, 1, 4, 4), datasource=DS_LOKI),
        piechart_panel(
            "Data Classification Distribution",
            [{"datasource": DS_LOKI, "expr": f'sum by (data_classification) (count_over_time({{{_tele} [1h]}}))', "legendFormat": "{{data_classification}}", "refId": "A", "instant": True}],
            grid=_grid(20, 1, 4, 4), datasource=DS_LOKI,
        ),

        row_panel("PII Over Time", y=5),
        timeseries_panel(
            "PII Detection Rate Over Time",
            [
                _loki_target(f'sum(count_over_time({{{_plog} | pii_detected="true" [5m]}})) / clamp_min(sum(count_over_time({{{_plog} [5m]}})),1) * 100', "PII %"),
            ],
            unit="percent", grid=_grid(0, 6, 12, 8), datasource=DS_LOKI,
        ),
        timeseries_panel(
            "PII Events by Tenant",
            [_loki_target(f'sum by (tenant_id) (count_over_time({{{_plog} | pii_detected="true" [5m]}})) or vector(0)', "{{tenant_id}}")],
            unit="short", grid=_grid(12, 6, 12, 8), datasource=DS_LOKI,
        ),
        barchart_panel(
            "PHI + PII Volume by Tenant (last 1h)",
            [{"datasource": DS_LOKI, "expr": f'sum by (tenant_id) (count_over_time({{{_tele} | data_classification=~"phi|pii" [1h]}}))', "legendFormat": "{{tenant_id}}", "refId": "A", "instant": True}],
            unit="short", grid=_grid(0, 14, 12, 8), datasource=DS_LOKI,
        ),
        timeseries_panel(
            "PHI Requests / min",
            [_loki_target(f'sum(count_over_time({{{_tele} | data_classification="phi" [1m]}})) or vector(0)', "PHI req/min")],
            unit="short", grid=_grid(12, 14, 12, 8), datasource=DS_LOKI,
        ),

        row_panel("Prompt Audit Log", y=22),
        logs_panel(
            "Prompt Log Events (PII-scrubbed)",
            f'{_plog} | tenant_id =~ "$tenant"',
            grid=_grid(0, 23, 24, 10), datasource=DS_LOKI,
        ),

        row_panel("High-Risk Request Table", y=33),
        table_panel(
            "PHI / PII Requests with Trace Links",
            [{"datasource": DS_LOKI,
              "expr": f'{_tele} | data_classification=~"phi|pii" | tenant_id=~"$tenant" | line_format "{{{{.request_id}}}} {{{{.tenant_id}}}} {{{{.model_name}}}} {{{{.trace_id}}}}"',
              "refId": "A"}],
            grid=_grid(0, 34, 24, 8), datasource=DS_LOKI,
        ),
    ]

    return dashboard(
        uid="ai-telemetry-safety",
        title="6 — AI Gateway: Safety & PII Compliance",
        description="PII detection rates, data classification, PHI/PII volumes, prompt audit log.",
        tags=["ai-telemetry", "safety", "pii", "compliance"],
        panels=panels,
    )


# ===========================================================================
# D A S H B O A R D   7 — Infrastructure & Runner Health
# ===========================================================================

def build_d7() -> dict:
    global _id_counter; _id_counter = 0

    panels = [
        row_panel("Pod & Replica Health", y=0),
        stat_panel("Running Pods",
                   'sum(kube_pod_status_phase{namespace="ai-gateway-ns", phase="Running"})',
                   unit="short", decimals=0,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":1},{"color":"green","value":2}],
                   grid=_grid(0, 1, 3, 4)),
        stat_panel("HPA Desired Replicas",
                   'kube_horizontalpodautoscaler_status_desired_replicas{namespace="ai-gateway-ns"}',
                   unit="short", decimals=0,
                   thresholds=[{"color":"blue","value":None}],
                   grid=_grid(3, 1, 3, 4)),
        stat_panel("Available Replicas",
                   'kube_deployment_status_replicas_available{namespace="ai-gateway-ns"}',
                   unit="short", decimals=0,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":1},{"color":"green","value":2}],
                   grid=_grid(6, 1, 3, 4)),
        stat_panel("Pod Restart Count (1h)",
                   'sum(increase(kube_pod_container_status_restarts_total{namespace="ai-gateway-ns"}[1h]))',
                   unit="short", decimals=0,
                   thresholds=[{"color":"green","value":None},{"color":"yellow","value":1},{"color":"red","value":5}],
                   grid=_grid(9, 1, 3, 4)),
        stat_panel("Node Memory Available",
                   'node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100',
                   unit="percent", decimals=1,
                   thresholds=[{"color":"red","value":None},{"color":"yellow","value":20},{"color":"green","value":40}],
                   grid=_grid(12, 1, 4, 4)),
        stat_panel("Node Ready",
                   'kube_node_status_condition{condition="Ready",status="True"}',
                   unit="short", decimals=0,
                   thresholds=[{"color":"red","value":None},{"color":"green","value":1}],
                   grid=_grid(16, 1, 4, 4)),
        alertlist_panel("Infrastructure Alerts", grid=_grid(20, 1, 4, 4)),

        row_panel("HPA Scaling", y=5),
        timeseries_panel(
            "HPA Current vs Desired Replicas",
            [
                _prom_target('kube_horizontalpodautoscaler_status_current_replicas{namespace="ai-gateway-ns"}', "current"),
                _prom_target('kube_horizontalpodautoscaler_status_desired_replicas{namespace="ai-gateway-ns"}', "desired"),
                _prom_target('kube_horizontalpodautoscaler_spec_min_replicas{namespace="ai-gateway-ns"}', "min"),
                _prom_target('kube_horizontalpodautoscaler_spec_max_replicas{namespace="ai-gateway-ns"}', "max"),
            ],
            unit="short", grid=_grid(0, 6, 12, 8),
        ),
        timeseries_panel(
            "Pod Restart Rate",
            [_prom_target('sum by (pod) (rate(kube_pod_container_status_restarts_total{namespace="ai-gateway-ns"}[15m]))', "{{pod}}")],
            unit="ops", grid=_grid(12, 6, 12, 8),
        ),

        row_panel("Container Resources", y=14),
        timeseries_panel(
            "Container Memory RSS by Pod",
            [_prom_target('container_memory_rss{namespace="ai-gateway-ns", container="ai-gateway"}', "{{pod}}")],
            unit="bytes", grid=_grid(0, 15, 12, 8),
        ),
        timeseries_panel(
            "Container CPU Usage by Pod",
            [_prom_target('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="ai-gateway-ns", container="ai-gateway"}[2m]))', "{{pod}}")],
            unit="percentunit", grid=_grid(12, 15, 12, 8),
        ),
        timeseries_panel(
            "Node Memory Available Over Time",
            [_prom_target('node_memory_MemAvailable_bytes', "available"), _prom_target('node_memory_MemTotal_bytes', "total")],
            unit="bytes", grid=_grid(0, 23, 12, 8),
        ),
        timeseries_panel(
            "Node CPU Usage (user mode)",
            [_prom_target('rate(node_cpu_seconds_total{mode="user"}[2m]) * 100', "cpu user %")],
            unit="percent", grid=_grid(12, 23, 12, 8),
        ),

        row_panel("Runner Self-Observability (NFR-014)", y=31),
        heatmap_panel(
            "Batch Duration Heatmap",
            'sum by (le) (rate(ai_telemetry_runner_batch_duration_seconds_bucket[2m]))',
            unit="s", grid=_grid(0, 32, 12, 8),
        ),
        timeseries_panel(
            "Kafka Queue Depth",
            [_prom_target('ai_telemetry_runner_kafka_queue_depth', "queue depth")],
            unit="short", grid=_grid(12, 32, 12, 8),
        ),
        timeseries_panel(
            "Publish Error Rate by Reason",
            [_prom_target('sum by (reason) (rate(ai_telemetry_runner_publish_errors_total[5m]))', "{{reason}}")],
            unit="ops", grid=_grid(0, 40, 12, 8),
        ),
        timeseries_panel(
            "Batch Duration p99",
            [_prom_target('histogram_quantile(0.99, sum by (le) (rate(ai_telemetry_runner_batch_duration_seconds_bucket[5m])))', "p99 batch duration")],
            unit="s", grid=_grid(12, 40, 12, 8),
        ),

        row_panel("OTel Collector Pipeline Health", y=48),
        timeseries_panel(
            "Collector Exporter Queue Size",
            [
                _prom_target('otelcol_exporter_queue_size{exporter="otlp/tempo"}',       "tempo queue"),
                _prom_target('otelcol_exporter_queue_size{exporter="prometheusremotewrite"}', "prom queue"),
                _prom_target('otelcol_exporter_queue_size{exporter="loki"}',             "loki queue"),
            ],
            unit="short", grid=_grid(0, 49, 12, 8),
        ),
        timeseries_panel(
            "Collector Dropped Spans / Logs",
            [
                _prom_target('rate(otelcol_processor_dropped_spans_total[5m])', "dropped spans/s"),
                _prom_target('rate(otelcol_processor_dropped_log_records_total[5m])', "dropped logs/s"),
            ],
            unit="ops", grid=_grid(12, 49, 12, 8),
        ),
    ]

    return dashboard(
        uid="ai-telemetry-infra",
        title="7 — AI Gateway: Infrastructure & Runner Health",
        description="Pod health, HPA scaling, container resources, runner self-metrics, OTel Collector health.",
        tags=["ai-telemetry", "infrastructure", "runner", "sre"],
        panels=panels,
    )


# ===========================================================================
# Generate
# ===========================================================================

if __name__ == "__main__":
    builders = [
        ("01-executive-overview.json",   build_d1),
        ("02-traffic-analytics.json",    build_d2),
        ("03-latency-performance.json",  build_d3),
        ("04-token-cost.json",           build_d4),
        ("05-model-quality.json",        build_d5),
        ("06-safety-pii.json",           build_d6),
        ("07-infra-runner.json",         build_d7),
    ]
    print("Generating Grafana dashboards…")
    for fname, builder in builders:
        d = builder()
        _save(fname, d)
    print(f"\nDone — {len(builders)} dashboards written to {OUT_DIR}/")
