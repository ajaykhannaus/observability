"""Grafana dashboard generator — produces 7 reshuffled dashboard JSON files.

Layout matches dashboards/dashboard_comparison.xlsx → "All Metrics Compare".

Run:
    python3 dashboards/generate_dashboards.py

Outputs (nav order 1–7):
    dashboards/01-infra-runner.json
    dashboards/02-latency-performance.json
    dashboards/03-model-quality.json
    dashboards/04-executive-overview.json
    dashboards/05-user-observability.json
    dashboards/06-token-cost.json
    dashboards/07-safety-pii.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
if OUT_DIR not in sys.path:
    sys.path.insert(0, OUT_DIR)

from metric_definitions import METRIC_DEFINITIONS  # noqa: E402

# ---------------------------------------------------------------------------
# Datasource references (match dashboards/provisioning/datasources.yaml UIDs)
# ---------------------------------------------------------------------------
PROM_UID = "prometheus-ds"
LOKI_UID = "loki-ds"
TEMPO_UID = "tempo-ds"

DS_PROMETHEUS = {"type": "prometheus", "uid": PROM_UID}
DS_LOKI       = {"type": "loki",       "uid": LOKI_UID}
DS_TEMPO      = {"type": "tempo",      "uid": TEMPO_UID}

# OTel Collector → Loki native OTLP ingestion: every log-record attribute
# (event_type, model_name, latency_ms, cost_usd, user_id, …) becomes Loki
# STRUCTURED METADATA, queryable directly as label filters / unwrap targets.
# The log line (body) is just the message string ("telemetry_event"), NOT JSON —
# so a `| json` stage throws JSONParserErr ("looks like object, can't find '}'")
# and breaks every panel. Filter the structured metadata directly instead.
_LOKI_STREAM = '{service_name=~".+"} |'


def _loki_ratio(numerator: str, denominator: str, scale: float = 100) -> str:
    """LogQL-safe ratio — clamp_min is Prometheus-only and breaks Loki panels."""
    return f"({numerator} / ({denominator} or on() vector(1))) * {scale}"

# ---------------------------------------------------------------------------
# Common template variables (shared by every dashboard)
# ---------------------------------------------------------------------------

def _ds_var(name: str, ds_type: str, label: str, uid: str) -> dict:
    return {
        "type": "datasource", "name": name, "label": label,
        "pluginId": ds_type, "multi": False, "includeAll": False,
        "hide": 2, "refresh": 1,
        "current": {"selected": True, "text": label, "value": uid},
    }

def _query_var(name: str, label: str, datasource: dict, query: str,
               multi: bool = True, include_all: bool = True,
               current: dict | None = None) -> dict:
    return {
        "type": "query", "name": name, "label": label,
        "datasource": datasource,
        "query": query,
        "multi": multi, "includeAll": include_all,
        "allValue": ".*", "hide": 0, "refresh": 2,
        "current": current or {},
        "sort": 1,
    }

def _custom_var(name: str, label: str, values: str,
                multi: bool = True, include_all: bool = True) -> dict:
    return {
        "type": "custom", "name": name, "label": label,
        "query": values,
        "multi": multi, "includeAll": include_all,
        "allValue": ".*", "hide": 0,
        "current": {"selected": True, "text": "All", "value": ".*"},
    }


# ---------------------------------------------------------------------------
# Per-dashboard filter sets — each dashboard exposes only relevant variables.
# ---------------------------------------------------------------------------

_PROM_LABEL_MAP: dict[str, str] = {
    "environment": 'environment=~"$environment"',
    "department":  'department=~"$department"',
    "region":      'region=~"$region"',
    "provider":    'model_provider=~"$provider"',
    "model":       'model_name=~"$model"',
    "operation":   'operation_name=~"$operation"',
    "status":      'status=~"$status"',
    "data_class":  'data_classification=~"$data_class"',
}

_LOKI_LABEL_MAP: dict[str, str] = {
    "department":  'department=~"$department"',
    "region":      'region=~"$region"',
    "provider":    'model_provider=~"$provider"',
    "model":       'model_name=~"$model"',
    "operation":   'operation_name=~"$operation"',
    "status":      'status=~"$status"',
    "data_class":  'data_classification=~"$data_class"',
}

_VAR_BUILDERS: dict[str, callable] = {
    "department": lambda: _query_var(
        "department", "Department", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, department)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "region": lambda: _query_var(
        "region", "Region", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, region)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "provider": lambda: _query_var(
        "provider", "Provider", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, model_provider)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "model": lambda: _query_var(
        "model", "Model", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, model_name)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "operation": lambda: _query_var(
        "operation", "Operation", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, operation_name)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "status": lambda: _query_var(
        "status", "Status", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, status)",
        current={"selected": True, "text": "All", "value": ".*"},
    ),
    "data_class": lambda: _custom_var(
        "data_class", "Data class", "phi,pii,confidential,internal",
    ),
    "environment": lambda: _query_var(
        "environment", "Environment", DS_PROMETHEUS,
        "label_values(ai_gateway_request_count_total, environment)",
        multi=False, include_all=False,
        current={"selected": True, "text": "dev", "value": "dev"},
    ),
}


class FilterSet:
    """Prometheus / Loki selector strings scoped to one dashboard's variables."""

    __slots__ = ("keys",)

    def __init__(self, *keys: str) -> None:
        unknown = set(keys) - _VAR_BUILDERS.keys()
        if unknown:
            raise ValueError(f"unknown filter keys: {unknown}")
        self.keys = keys

    def variables(self) -> list[dict]:
        return [_VAR_BUILDERS[k]() for k in self.keys]

    @property
    def prom_labels(self) -> str:
        return ",".join(_PROM_LABEL_MAP[k] for k in self.keys if k in _PROM_LABEL_MAP)

    @property
    def prom(self) -> str:
        labels = self.prom_labels
        return "{" + labels + "}" if labels else ""

    @property
    def prom_error(self) -> str:
        parts = [_PROM_LABEL_MAP[k] for k in self.keys if k in _PROM_LABEL_MAP and k != "status"]
        parts.append('status="error"')
        return "{" + ",".join(parts) + "}"

    @property
    def loki(self) -> str:
        parts = [_LOKI_LABEL_MAP[k] for k in self.keys if k in _LOKI_LABEL_MAP]
        return ("| " + " | ".join(parts)) if parts else ""

    def loki_except(self, *exclude: str) -> str:
        """Loki pipeline filters omitting keys (e.g. skip department on a by-department panel)."""
        skip = set(exclude)
        parts = [_LOKI_LABEL_MAP[k] for k in self.keys if k in _LOKI_LABEL_MAP and k not in skip]
        return ("| " + " | ".join(parts)) if parts else ""

    @property
    def login_loki(self) -> str:
        """Login events — department + region only (when present on dashboard)."""
        parts = [
            _LOKI_LABEL_MAP[k]
            for k in self.keys
            if k in ("department", "region")
        ]
        return ("| " + " | ".join(parts)) if parts else ""


def _token_selector(token_type: str, f: FilterSet) -> str:
    labels = f.prom_labels
    if labels:
        return "{" + f'token_type="{token_type}",' + labels + "}"
    return "{" + f'token_type="{token_type}"' + "}"


# Dashboard-specific filter presets (order = top-bar left → right).
FILTERS_D1 = ("department", "region", "provider", "model", "environment")
FILTERS_D2 = ("department", "region", "provider", "model", "operation", "status", "environment")
FILTERS_D3 = ("department", "region", "provider", "model", "operation", "environment")
FILTERS_D4 = ("department", "region", "provider", "model", "environment")
FILTERS_D5 = ("department", "provider", "model", "operation", "environment")
FILTERS_D6 = ("department", "data_class", "provider", "model", "environment")
FILTERS_D7 = ("environment", "department", "model")
FILTERS_D8 = ("department", "region", "provider", "model", "operation", "environment")
FILTERS_D9 = ("department", "region", "provider", "model", "environment")

# Map dashboard UID → filter keys (used for nav links that reset template variables).
FILTERS_BY_UID: dict[str, tuple[str, ...]] = {
    "ai-telemetry-infra": FILTERS_D7,
    "ai-telemetry-latency": FILTERS_D3,
    "ai-telemetry-quality": FILTERS_D5,
    "ai-telemetry-executive": FILTERS_D2,
    "ai-telemetry-users": FILTERS_D9,
    "ai-telemetry-cost": FILTERS_D8,
    "ai-telemetry-safety": FILTERS_D6,
}

_ALL_TEMPLATE_VARS = (
    "department", "region", "provider", "model", "operation", "status", "data_class", "environment",
)


def _nav_var_reset_query() -> str:
    """Reset every template variable in the URL (Grafana SPA keeps stale var-* params otherwise)."""
    parts: list[str] = []
    for key in _ALL_TEMPLATE_VARS:
        if key == "environment":
            parts.append("var-environment=dev")
        else:
            parts.append(f"var-{key}=All")
    return "&".join(parts)


def _dashboard_href(uid: str) -> str:
    """Dashboard link that clears inherited template variables from other dashboards."""
    return f"/d/{uid}?{_nav_var_reset_query()}"


_id_counter = 0

def _next_id() -> int:
    global _id_counter
    _id_counter += 1
    return _id_counter


def _grid(x: int, y: int, w: int, h: int) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def _panel_description(title: str, description: str | None = None) -> str | None:
    """Resolve panel description for Grafana ⓘ tooltip (metric_definitions or override)."""
    if description is not None:
        return description
    return METRIC_DEFINITIONS.get(title)


def _with_description(panel: dict, title: str, description: str | None = None) -> dict:
    desc = _panel_description(title, description)
    if desc:
        panel["description"] = desc
    return panel


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
    description: str | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    th = thresholds or [
        {"color": "green",  "value": None},
        {"color": "yellow", "value": 80},
        {"color": "red",    "value": 95},
    ]
    return _with_description({
        "id": _next_id(), "type": "stat", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit, "decimals": decimals,
                # Grafana 11: field color mode must be thresholds/shades/etc — not "background".
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": th},
                "mappings": mappings or [],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": color_mode,
            "graphMode": "area",
            "justifyMode": "center",
            "textMode": "auto",
            "wideLayout": True,
            "showPercentChange": False,
            "percentChangeColorMode": "standard",
            "text": {},
        },
        "gridPos": grid or _grid(0, 0, 4, 3),
        "targets": [_loki_stat_target(expr, ref="A") if ds == DS_LOKI
                    else _prom_stat_target(expr)],
    }, title, description)


def timeseries_panel(
    title: str,
    targets: list[dict],
    unit: str = "short",
    grid: dict | None = None,
    datasource: dict | None = None,
    stacking: str = "none",
    fill_opacity: int = 5,
    legend_placement: str = "bottom",
    axis_soft_max: float | None = None,
    decimals: int | None = None,
    description: str | None = None,
    thresholds: list[dict] | None = None,
    thresholds_style: str | None = None,
    legend_calcs: list[str] | None = None,
    legend_display_mode: str = "list",
) -> dict:
    ds = datasource or DS_PROMETHEUS
    # Stacked area looks best fully filled; line charts get a soft gradient wash.
    if fill_opacity == 5:
        fill_opacity = 18 if stacking == "none" else 35
    custom: dict[str, Any] = {
        "lineWidth": 2,
        "fillOpacity": fill_opacity,
        "gradientMode": "opacity",
        "lineInterpolation": "smooth",
        "spanNulls": True,
        "stacking": {"mode": stacking, "group": "A"},
        "showPoints": "never",
        "pointSize": 5,
        "axisColorMode": "text",
        "axisBorderShow": False,
        "axisGridShow": True,
        "lineStyle": {"fill": "solid"},
        "scaleDistribution": {"type": "linear"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    if axis_soft_max is not None:
        custom["axisSoftMax"] = axis_soft_max
    if thresholds_style:
        custom["thresholdsStyle"] = {"mode": thresholds_style}
    defaults: dict[str, Any] = {"unit": unit, "custom": custom}
    if decimals is not None:
        defaults["decimals"] = decimals
    if thresholds:
        defaults["color"] = {"mode": "thresholds"}
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
    else:
        # Distinct, high-contrast series colours straight out of the box.
        defaults["color"] = {"mode": "palette-classic"}
    legend: dict[str, Any] = {
        "displayMode": legend_display_mode,
        "placement": legend_placement,
        "showLegend": True,
    }
    # Tables get rich summary stats so leadership can read values without hovering.
    if legend_calcs:
        legend["calcs"] = legend_calcs
    elif legend_display_mode == "table":
        legend["calcs"] = ["lastNotNull", "mean", "max"]
    return _with_description({
        "id": _next_id(), "type": "timeseries", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": defaults,
            "overrides": [],
        },
        "options": {
            "legend": legend,
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": targets,
    }, title, description)


def _prom_targets(*specs: tuple[str, str]) -> list[dict]:
    """Build Prometheus targets with unique refIds (A, B, C, …)."""
    refs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [_prom_target(expr, legend, refs[i]) for i, (expr, legend) in enumerate(specs)]


def _prom_stat_target(expr: str, ref: str = "A") -> dict:
    """Prometheus stat/gauge targets — range queries; instant rate/recording rules often return empty."""
    return {
        "datasource": DS_PROMETHEUS, "expr": expr,
        "refId": ref, "range": True, "instant": False,
    }


def _prom_target(expr: str, legend: str, ref: str = "A") -> dict:
    return {
        "datasource": DS_PROMETHEUS, "expr": expr,
        "legendFormat": legend, "refId": ref,
        "format": "time_series", "range": True,
    }


def _loki_targets(*specs: tuple[str, str]) -> list[dict]:
    """Build Loki range targets with unique refIds (A, B, C, …)."""
    refs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [_loki_target(expr, legend, refs[i]) for i, (expr, legend) in enumerate(specs)]


def _loki_target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {
        "datasource": DS_LOKI, "expr": expr,
        "legendFormat": legend, "refId": ref,
        "queryType": "range",
    }


def _loki_instant_target(expr: str, legend: str = "", ref: str = "A") -> dict:
    """Loki metric queries used by bar/pie panels need explicit instant queryType."""
    return {
        "datasource": DS_LOKI, "expr": expr,
        "legendFormat": legend, "refId": ref,
        "instant": True, "queryType": "instant",
    }


def _loki_stat_target(expr: str, ref: str = "A") -> dict:
    """Loki stat panels — range metric queries; instant often returns empty."""
    return {
        "datasource": DS_LOKI, "expr": expr,
        "refId": ref, "queryType": "range",
        "instant": False, "range": True,
    }


def gauge_panel(
    title: str, expr: str, unit: str = "percent",
    min_val: float = 0, max_val: float = 100,
    thresholds: list[dict] | None = None,
    grid: dict | None = None,
    datasource: dict | None = None,
    description: str | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    th = thresholds or [
        {"color": "red",    "value": None},
        {"color": "yellow", "value": 25},
        {"color": "green",  "value": 50},
    ]
    return _with_description({
        "id": _next_id(), "type": "gauge", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit, "min": min_val, "max": max_val,
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": th},
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "orientation": "auto",
            "sizing": "auto",
        },
        "gridPos": grid or _grid(0, 0, 6, 6),
        "targets": [_loki_stat_target(expr) if ds == DS_LOKI
                    else _prom_stat_target(expr)],
    }, title, description)


def barchart_panel(
    title: str, targets: list[dict], unit: str = "short",
    grid: dict | None = None, datasource: dict | None = None,
    orientation: str = "auto",
    description: str | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    return _with_description({
        "id": _next_id(), "type": "barchart", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "fillOpacity": 80,
                    "gradientMode": "opacity",
                    "lineWidth": 1,
                    "axisColorMode": "text",
                    "axisBorderShow": False,
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                },
            },
            "overrides": [],
        },
        "options": {
            "orientation": orientation,
            "xTickLabelRotation": -45,
            "xTickLabelSpacing": 100,
            "barRadius": 0.15,
            "groupWidth": 0.7,
            "barWidth": 0.9,
            "fullHighlight": False,
            "showValue": "auto",
            "stacking": "none",
            "tooltip": {"mode": "single", "sort": "desc"},
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
        },
        "gridPos": grid or _grid(0, 0, 12, 8),
        "targets": targets,
    }, title, description)


def piechart_panel(
    title: str, targets: list[dict],
    grid: dict | None = None, datasource: dict | None = None,
    pie_type: str = "pie",
    unit: str = "short",
    description: str | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    return _with_description({
        "id": _next_id(), "type": "piechart", "title": title,
        "datasource": ds,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}},
            },
            "overrides": [],
        },
        "options": {
            "pieType": pie_type,
            "displayLabels": [],
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                       "values": ["value"]},
            "tooltip": {"mode": "single", "sort": "desc"},
        },
        "gridPos": grid or _grid(0, 0, 8, 8),
        "targets": targets,
    }, title, description)


def bargauge_panel(
    title: str, expr: str, unit: str = "percent",
    grid: dict | None = None,
    thresholds: list[dict] | None = None,
    datasource: dict | None = None,
    legend_format: str = "{{model_name}}",
    color_mode: str = "thresholds",
    decimals: int | None = None,
    description: str | None = None,
) -> dict:
    ds = datasource or DS_PROMETHEUS
    th = thresholds or [
        {"color": "green",  "value": None},
        {"color": "yellow", "value": 75},
        {"color": "red",    "value": 90},
    ]
    defaults: dict[str, Any] = {"unit": unit}
    if decimals is not None:
        defaults["decimals"] = decimals
    if color_mode == "palette":
        defaults["color"] = {"mode": "palette-classic"}
    else:
        defaults["color"] = {"mode": "thresholds"}
        defaults["thresholds"] = {"mode": "absolute", "steps": th}
    return _with_description({
        "id": _next_id(), "type": "bargauge", "title": title,
        "datasource": ds,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "displayMode": "gradient",
            "valueMode": "color",
            "showUnfilled": True,
            "namePlacement": "left",
            "minVizWidth": 8,
            "minVizHeight": 16,
        },
        "gridPos": grid or _grid(0, 0, 12, 6),
        "targets": (
            [_loki_instant_target(expr, legend_format)]
            if ds == DS_LOKI
            else [{"datasource": ds, "expr": expr, "instant": True,
                     "legendFormat": legend_format, "refId": "A"}]
        ),
    }, title, description)


def heatmap_panel(
    title: str, expr: str, unit: str = "ms",
    grid: dict | None = None,
    description: str | None = None,
) -> dict:
    return _with_description({
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
    }, title, description)


def table_panel(
    title: str, targets: list[dict],
    grid: dict | None = None,
    datasource: dict | None = None,
    description: str | None = None,
) -> dict:
    ds = datasource or DS_LOKI
    return _with_description({
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
    }, title, description)


def logs_panel(
    title: str, expr: str,
    grid: dict | None = None,
    datasource: dict | None = None,
    description: str | None = None,
) -> dict:
    ds = datasource or DS_LOKI
    return _with_description({
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
        "targets": [{
            "datasource": ds,
            "expr": expr,
            "refId": "A",
            "queryType": "range",
            "maxLines": 500,
            "legendFormat": "",
        }],
    }, title, description)


def alertlist_panel(
    title: str, grid: dict | None = None, description: str | None = None,
) -> dict:
    return _with_description({
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
    }, title, description)


def row_panel(title: str, y: int, collapsed: bool = False) -> dict:
    """Legacy flat row header — prefer accordion_section() for collapsible groups."""
    return {
        "id": _next_id(), "type": "row", "title": title,
        "collapsed": collapsed, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def _normalize_section_panels(panels: list[dict]) -> list[dict]:
    """Shift section panels so ymin=0 (Grafana nested-row convention)."""
    if not panels:
        return []
    ymin = min(p["gridPos"]["y"] for p in panels)
    out: list[dict] = []
    for panel in panels:
        cp = dict(panel)
        gp = dict(panel["gridPos"])
        gp["y"] = gp["y"] - ymin
        cp["gridPos"] = gp
        out.append(cp)
    return out


def accordion_section(
    title: str,
    panels: list[dict],
    *,
    collapsed: bool = False,
    description: str | None = None,
) -> dict:
    """Grafana row with nested child panels — true click-to-expand accordion."""
    row: dict[str, Any] = {
        "id": _next_id(),
        "type": "row",
        "title": title,
        "collapsed": collapsed,
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1},
        "panels": _normalize_section_panels(panels),
    }
    if description:
        row["description"] = description
    return row


def build_dashboard_panels(
    headline_stats: list[dict] | None,
    sections: list[
        tuple[str, list[dict], bool]
        | tuple[str, list[dict], bool, str]
    ],
) -> list[dict]:
    """Compose always-visible headline stats + collapsible accordion sections."""
    result: list[dict] = []
    y = 0

    if headline_stats:
        ymin = min(p["gridPos"]["y"] for p in headline_stats)
        ymax = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in headline_stats)
        for panel in headline_stats:
            cp = dict(panel)
            gp = dict(panel["gridPos"])
            gp["y"] = y + (gp["y"] - ymin)
            cp["gridPos"] = gp
            result.append(cp)
        y += ymax - ymin

    for entry in sections:
        if len(entry) == 4:
            title, section_panels, collapsed, desc = entry
        else:
            title, section_panels, collapsed = entry
            desc = None
        row = accordion_section(title, section_panels, collapsed=collapsed, description=desc)
        row["gridPos"] = {"x": 0, "y": y, "w": 24, "h": 1}
        result.append(row)
        y += 1

    return result


def _assign_panel_ids(panels: list[dict], start_id: int = 1) -> int:
    """Assign unique IDs to top-level and nested row children."""
    next_id = start_id
    for panel in panels:
        panel["id"] = next_id
        next_id += 1
        if panel.get("type") == "row" and panel.get("panels"):
            next_id = _assign_panel_ids(panel["panels"], next_id)
    return next_id


def _count_panels(panels: list[dict]) -> int:
    total = 0
    for panel in panels:
        total += 1
        if panel.get("type") == "row" and panel.get("panels"):
            total += _count_panels(panel["panels"])
    return total


def text_panel(
    title: str,
    content: str,
    grid: dict | None = None,
    mode: str = "markdown",
    transparent: bool = False,
    description: str | None = None,
) -> dict:
    panel: dict[str, Any] = {
        "id": _next_id(), "type": "text", "title": title,
        "options": {"content": content, "mode": mode},
        "gridPos": grid or _grid(0, 0, 24, 2),
        "targets": [],
    }
    if transparent:
        panel["transparent"] = True
    return _with_description(panel, title, description)


# ---------------------------------------------------------------------------
# Cross-dashboard navigation (visible button bar + compact header dropdown)
# ---------------------------------------------------------------------------

NAV_BAR_HEIGHT = 3

DASHBOARDS_NAV: list[tuple[str, str, str]] = [
    ("1", "Infrastructure", "ai-telemetry-infra"),
    ("2", "Network", "ai-telemetry-latency"),
    ("3", "AI", "ai-telemetry-quality"),
    ("4", "Data", "ai-telemetry-executive"),
    ("5", "User", "ai-telemetry-users"),
    ("6", "Cost & Usage", "ai-telemetry-cost"),
    ("7", "Safety & Security", "ai-telemetry-safety"),
]


def _shift_panels_y(panels: list[dict], delta: int) -> list[dict]:
    shifted: list[dict] = []
    for panel in panels:
        p = dict(panel)
        gp = dict(panel["gridPos"])
        gp["y"] = gp["y"] + delta
        p["gridPos"] = gp
        shifted.append(p)
    return shifted


def nav_bar_panel(current_uid: str) -> dict:
    """HTML button row — primary navigation; current dashboard highlighted."""
    buttons: list[str] = []
    for num, label, uid in DASHBOARDS_NAV:
        active = uid == current_uid
        if active:
            style = (
                "background:#1d4ed8;color:#fff;border:2px solid #1e40af;"
                "box-shadow:0 1px 3px rgba(30,64,175,.35);"
            )
        else:
            style = (
                "background:#eff6ff;color:#1e3a8a;border:1px solid #93c5fd;"
            )
        buttons.append(
            f'<a href="{_dashboard_href(uid)}" style="display:inline-flex;align-items:center;'
            f"padding:9px 14px;margin:0;border-radius:8px;font-size:13px;"
            f"font-weight:600;text-decoration:none;white-space:nowrap;"
            f'letter-spacing:.01em;{style}">{num}. {label}</a>'
        )
    content = (
        '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;'
        'justify-content:flex-start;width:100%;padding:6px 2px 2px;">'
        + "".join(buttons)
        + "</div>"
    )
    return text_panel(
        "",
        content,
        grid=_grid(0, 0, 24, NAV_BAR_HEIGHT),
        mode="html",
        transparent=True,
    )


def _prepend_nav(uid: str, panels: list[dict],
                 title: str = "", subtitle: str = "") -> list[dict]:
    nav = nav_bar_panel(uid)
    return [nav] + _shift_panels_y(panels, NAV_BAR_HEIGHT)


def _dashboard_nav_links() -> list[dict]:
    """Compact header dropdown only — button row lives in the nav_bar_panel."""
    return [
        {
            "title": "All dashboards",
            "tooltip": "Jump to any AI Telemetry dashboard",
            "type": "dashboards",
            "icon": "apps",
            "tags": ["ai-telemetry"],
            "asDropdown": True,
            "includeVars": False,
            "keepTime": True,
            "targetBlank": False,
        },
    ]


# ---------------------------------------------------------------------------
# Dashboard skeleton
# ---------------------------------------------------------------------------

def dashboard(
    uid: str, title: str, description: str, tags: list[str],
    panels: list[dict],
    variables: list[dict],
    refresh: str = "30s",
) -> dict:
    global _id_counter
    _id_counter = 0   # reset per dashboard so IDs start at 1
    panels = _prepend_nav(uid, panels, title=title, subtitle=description)
    _assign_panel_ids(panels)
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": tags,
        "schemaVersion": 39,
        "version": 2,
        "style": "light",
        "refresh": refresh,
        "time": {"from": "now-5m", "to": "now"},
        "timepicker": {
            "refresh_intervals": ["10s", "30s", "1m", "5m", "15m", "30m", "1h"],
        },
        "timezone": "browser",
        "editable": True,
        "fiscalYearStartMonth": 0,
        "liveNow": False,
        "weekStart": "",
        # Shared crosshair across panels — synchronised hover for storytelling.
        "graphTooltip": 1,
        "templating": {"list": variables},
        "annotations": {"list": []},
        "panels": panels,
        "links": _dashboard_nav_links(),
    }


def _save(name: str, d: dict) -> None:
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    print(f"  wrote {name}  ({_count_panels(d['panels'])} panels)")


# ===========================================================================
# D A S H B O A R D   1 — Request & Traffic Metrics
# ===========================================================================

_RPM_TOPK = 6


def _rpm_rate(
    filter_suffix: str,
    by: str | None = None,
    topk_n: int | None = None,
    metric: str = "ai_gateway_request_count_total",
) -> str:
    """Requests/min using Grafana's auto-tuned $__rate_interval window."""
    inner = f"rate({metric}{filter_suffix}[$__rate_interval])"
    expr = f"sum by ({by}) ({inner})" if by else f"sum({inner})"
    if topk_n:
        expr = f"topk({topk_n}, {expr})"
    return f"{expr} * 60"


def _rpm_share(filter_suffix: str, by: str = "model_name", topk_n: int = _RPM_TOPK) -> str:
    """Each series' share of total RPM (percent)."""
    total = f"sum(rate(ai_gateway_request_count_total{filter_suffix}[$__rate_interval]))"
    by_label = f"sum by ({by}) (rate(ai_gateway_request_count_total{filter_suffix}[$__rate_interval]))"
    return f"100 * topk({topk_n}, {by_label}) / clamp_min({total}, 1e-9)"


def _error_rate_by_model(filter_suffix: str, topk_n: int = _RPM_TOPK) -> str:
    """Failed requests as a percentage per model (top N by error rate)."""
    errors = (
        f"sum by (model_name) (rate(ai_gateway_exception_count_total"
        f"{filter_suffix}[$__rate_interval]))"
    )
    requests = (
        f"sum by (model_name) (rate(ai_gateway_request_count_total"
        f"{filter_suffix}[$__rate_interval]))"
    )
    return f"topk({topk_n}, {errors} / clamp_min({requests}, 1e-9) * 100)"


def build_d1() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D2)
    _f = F.prom
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    headline = [
        stat_panel(
            "Total requests",
            f'sum(increase(ai_gateway_request_count_total{_f}[$__range]))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(0, 0, 24, 5),
            description=(
                "Single headline count for the selected time range with a sparkline trend. "
                "Source: Prometheus `increase(ai_gateway_request_count_total[$__range])`."
            ),
        ),
    ]

    panels = build_dashboard_panels(headline, [
        ("Traffic", [
            timeseries_panel(
                "RPM",
                [_prom_target(_rpm_rate(_f), "Requests / min", "A")],
                unit="r/min", decimals=1,
                fill_opacity=20,
                grid=_grid(0, 0, 24, 7),
                description=(
                    "Continuous requests-per-minute rate over time. "
                    "Source: `rate(ai_gateway_request_count_total[$__rate_interval]) × 60`."
                ),
            ),
            timeseries_panel(
                "RPM by Model",
                [_prom_target(
                    _rpm_rate(_f, by="model_name", topk_n=_RPM_TOPK),
                    "{{model_name}}", "A",
                )],
                unit="r/min", decimals=1,
                stacking="normal",
                fill_opacity=45,
                legend_display_mode="table",
                legend_calcs=["lastNotNull", "mean"],
                grid=_grid(0, 7, 12, 8),
                description=(
                    "Stacked area chart — top 6 models by RPM. "
                    "Uses `$__rate_interval` for consistent smoothing across zoom levels."
                ),
            ),
            bargauge_panel(
                "RPM by Model (current)",
                f"sort_desc({_rpm_rate(_f, by='model_name', topk_n=_RPM_TOPK)})",
                unit="r/min",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(12, 7, 6, 8),
                description="Top 6 models by current requests/min — ranked bar gauge snapshot.",
            ),
            piechart_panel(
                "RPM Share by Model",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f"sort_desc({_rpm_share(_f)})",
                  "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
                pie_type="donut",
                unit="percent",
                grid=_grid(18, 7, 6, 8),
                description=(
                    "Each model's percentage of total RPM (top 6). "
                    "Companion to the stacked area — shows share, not absolute volume."
                ),
            ),
        ], False, "Request rate, model split, and share"),
        ("Errors", [
            timeseries_panel(
                "Error RPM by Model",
                [_prom_target(
                    _rpm_rate(_f, by="model_name", topk_n=_RPM_TOPK,
                              metric="ai_gateway_exception_count_total"),
                    "{{model_name}} errors", "A",
                )],
                unit="r/min", decimals=1,
                fill_opacity=25,
                legend_display_mode="table",
                legend_calcs=["lastNotNull", "mean"],
                grid=_grid(0, 0, 24, 6),
                description=(
                    "Failed requests per minute per model (top 6). "
                    "Source: `ai_gateway_exception_count_total`."
                ),
            ),
            timeseries_panel(
                "Error Rate by Model",
                [_prom_target(_error_rate_by_model(_f), "{{model_name}}", "A")],
                unit="percent", decimals=1,
                axis_soft_max=100,
                legend_display_mode="table",
                legend_calcs=["lastNotNull"],
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 5},
                    {"color": "red", "value": 10},
                ],
                thresholds_style="line+area",
                grid=_grid(0, 6, 24, 8),
                description=(
                    "Failed requests as a percentage per model. "
                    "Yellow/red bands mark 5% / 10% SLA breach territory."
                ),
            ),
        ], False, "Failed requests per minute and error rate by model"),
        ("Model mix", [
            piechart_panel(
                "Model Provider Distribution",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (model_provider) (increase(ai_gateway_request_count_total{_f}[1h])))',
                  "legendFormat": "{{model_provider}}", "refId": "A", "instant": True}],
                pie_type="donut",
                grid=_grid(0, 0, 12, 8),
            ),
            bargauge_panel(
                "Model Distribution (last 1h)",
                f'sort_desc(sum by (model_name) (increase(ai_gateway_request_count_total{_f}[1h])))',
                unit="short",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(12, 0, 12, 8),
            ),
        ], True, "Provider and model distribution over the last hour"),
        ("Traffic Volume", [
            timeseries_panel(
                "Requests / min by Model",
                [_prom_target(f'sum by (model_name) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{model_name}}")],
                unit="r/min", grid=_grid(0, 0, 12, 8),
            ),
            timeseries_panel(
                "Requests / min by Department",
                [_prom_target(f'sum by (department) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{department}}")],
                unit="r/min", grid=_grid(12, 0, 12, 8),
            ),
            timeseries_panel(
                "Requests / min by Operation",
                [_prom_target(f'sum by (operation_name) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{operation_name}}")],
                unit="r/min", grid=_grid(0, 8, 12, 8),
            ),
            piechart_panel(
                "Model Distribution (last 1h)",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (model_name) (increase(ai_gateway_request_count_total{_f}[1h])))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
                grid=_grid(12, 8, 6, 8),
            ),
            piechart_panel(
                "Model Provider Distribution",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (model_provider) (increase(ai_gateway_request_count_total{_f}[1h])))', "legendFormat": "{{model_provider}}", "refId": "A", "instant": True}],
                grid=_grid(18, 8, 6, 8),
            ),
        ], True, "Traffic volume by model, department, and operation"),
        ("Errors & Retries", [
            timeseries_panel(
                "Error Rate by Type",
                [_prom_target(f'sum by (error_type) (rate(ai_gateway_exception_count_total{_f}[5m]))', "{{error_type}}")],
                unit="reqps", grid=_grid(0, 0, 12, 8),
            ),
            barchart_panel(
                "Errors by HTTP Status Code (last 1h)",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (http_status) (increase(ai_gateway_exception_count_total{_f}[1h])))', "legendFormat": "{{http_status}}", "refId": "A", "instant": True}],
                unit="short", grid=_grid(12, 0, 8, 8),
            ),
            piechart_panel(
                "Error Category Mix",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (error_category) (increase(ai_gateway_exception_count_total{_f}[1h])))', "legendFormat": "{{error_category}}", "refId": "A", "instant": True}],
                grid=_grid(20, 0, 4, 8),
            ),
        ], True, "Error types, HTTP status codes, and categories"),
        ("SLA Compliance", [
            timeseries_panel(
                "SLA Breach Rate by Department",
                [_prom_target(
                    f'sum by (department) (rate(ai_gateway_request_count_total{F.prom_error}[5m])) '
                    f'/ clamp_min(sum by (department) (rate(ai_gateway_request_count_total{_f}[5m])),1e-9) * 100',
                    "{{department}}",
                )],
                unit="percent", grid=_grid(0, 0, 12, 8),
            ),
            barchart_panel(
                "Total Requests by Routing Reason (last 1h)",
                [_loki_instant_target(
                    f'sum by (routing_reason) (count_over_time({_LOKI_STREAM} event_type="telemetry_event" {F.loki} [1h]))',
                    "{{routing_reason}}",
                )],
                unit="short", grid=_grid(12, 0, 12, 8),
                datasource=DS_LOKI,
            ),
            gauge_panel(
                "SLA attainment %",
                f'100 - (sum(rate(ai_gateway_request_count_total{F.prom_error}[5m])) '
                f'/ clamp_min(sum(rate(ai_gateway_request_count_total{_f}[5m])), 1e-9) * 100)',
                unit="percent", min_val=90, max_val=100,
                thresholds=[
                    {"color": "red", "value": None},
                    {"color": "yellow", "value": 95},
                    {"color": "green", "value": 99},
                ],
                grid=_grid(0, 8, 8, 8),
            ),
        ], True, "Department SLA breach rate, routing mix, and attainment"),
        ("Live Request Log", [
            stat_panel(
                "Error budget burn (5m)",
                f'sum(rate(ai_gateway_request_count_total{F.prom_error}[5m])) '
                f'/ clamp_min(sum(rate(ai_gateway_request_count_total{_f}[5m])), 1e-9) * 100',
                unit="percent", decimals=2,
                thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 2}, {"color": "red", "value": 5}],
                color_mode="value",
                grid=_grid(0, 0, 8, 5),
            ),
            barchart_panel(
                "Top models by traffic (1h)",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'topk(6, sum by (model_name) (increase(ai_gateway_request_count_total{_f}[1h])))',
                  "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
                unit="short", grid=_grid(8, 0, 16, 5),
            ),
            logs_panel(
                "Live Telemetry Events",
                f'{_LOKI_STREAM} event_type="telemetry_event" '
                f'{F.loki} '
                '| line_format "{{.timestamp}} [{{.model_name}}] {{.operation_name}} '
                'status={{.status}} lat={{.latency_ms}}ms dept={{.department}} '
                'routing={{.routing_reason}}"',
                grid=_grid(0, 5, 24, 10),
            ),
        ], True, "Error budget, top models, and real-time event stream"),
    ])

    return dashboard(
        uid="ai-telemetry-executive",
        title="4. Data observability",
        description="Request volume, RPM, traffic mix, model distribution, errors, and SLA compliance.",
        tags=["ai-telemetry", "traffic", "requests", "data"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   2 — Traffic & Request Analytics
# ===========================================================================

def build_d2() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D2)
    _f = F.prom

    panels = build_dashboard_panels(None, [
        ("Traffic Volume", [
            timeseries_panel(
                "Requests / min by Model",
                [_prom_target(f'sum by (model_name) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{model_name}}")],
                unit="r/min", grid=_grid(0, 0, 12, 8),
            ),
            timeseries_panel(
                "Requests / min by Department",
                [_prom_target(f'sum by (department) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{department}}")],
                unit="r/min", grid=_grid(12, 0, 12, 8),
            ),
            timeseries_panel(
                "Requests / min by Operation",
                [_prom_target(f'sum by (operation_name) (rate(ai_gateway_request_count_total{_f}[2m])) * 60', "{{operation_name}}")],
                unit="r/min", grid=_grid(0, 8, 12, 8),
            ),
            piechart_panel(
                "Model Distribution (last 1h)",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (model_name) (increase(ai_gateway_request_count_total{_f}[1h])))', "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
                grid=_grid(12, 8, 6, 8),
            ),
            piechart_panel(
                "Model Provider Distribution",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (model_provider) (increase(ai_gateway_request_count_total{_f}[1h])))', "legendFormat": "{{model_provider}}", "refId": "A", "instant": True}],
                grid=_grid(18, 8, 6, 8),
            ),
        ], False, "Request volume by model, department, and operation"),
        ("Errors & Retries", [
            timeseries_panel(
                "Error Rate by Type",
                [_prom_target(f'sum by (error_type) (rate(ai_gateway_exception_count_total{_f}[5m]))', "{{error_type}}")],
                unit="reqps", grid=_grid(0, 0, 12, 8),
            ),
            barchart_panel(
                "Errors by HTTP Status Code (last 1h)",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (http_status) (increase(ai_gateway_exception_count_total{_f}[1h])))', "legendFormat": "{{http_status}}", "refId": "A", "instant": True}],
                unit="short", grid=_grid(12, 0, 8, 8),
            ),
            piechart_panel(
                "Error Category Mix",
                [{"datasource": DS_PROMETHEUS, "expr": f'sort_desc(sum by (error_category) (increase(ai_gateway_exception_count_total{_f}[1h])))', "legendFormat": "{{error_category}}", "refId": "A", "instant": True}],
                grid=_grid(20, 0, 4, 8),
            ),
        ], True, "Error taxonomy and HTTP status breakdown"),
        ("SLA Compliance", [
            timeseries_panel(
                "SLA Breach Rate by Department",
                [_prom_target(
                    f'sum by (department) (rate(ai_gateway_request_count_total{F.prom_error}[5m])) '
                    f'/ clamp_min(sum by (department) (rate(ai_gateway_request_count_total{_f}[5m])),1e-9) * 100',
                    "{{department}}",
                )],
                unit="percent", grid=_grid(0, 0, 12, 8),
            ),
            barchart_panel(
                "Total Requests by Routing Reason (last 1h)",
                [_loki_instant_target(
                    f'sum by (routing_reason) (count_over_time({_LOKI_STREAM} event_type="telemetry_event" {F.loki} [1h]))',
                    "{{routing_reason}}",
                )],
                unit="short", grid=_grid(12, 0, 12, 8),
                datasource=DS_LOKI,
            ),
        ], True, "Department SLA breach rate and routing mix"),
        ("Live Request Log", [
            logs_panel(
                "Live Telemetry Events",
                f'{_LOKI_STREAM} event_type="telemetry_event" '
                f'{F.loki} '
                '| line_format "{{.timestamp}} [{{.model_name}}] {{.operation_name}} '
                'status={{.status}} lat={{.latency_ms}}ms dept={{.department}} '
                'routing={{.routing_reason}}"',
                grid=_grid(0, 0, 24, 10),
            ),
        ], True, "Real-time telemetry event stream"),
    ])

    return dashboard(
        uid="ai-telemetry-traffic",
        title="2. Traffic & Request Analytics",
        description="Request volumes, model usage, error taxonomy, SLA compliance.",
        tags=["ai-telemetry", "traffic", "errors"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   3 — Latency & Performance Metrics
# ===========================================================================

def build_d3() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D3)
    _f = F.prom
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    panels = build_dashboard_panels(None, [
        ("Latency", [
            timeseries_panel(
                "End-to-end response latency",
                [_prom_target(
                    f'sum(rate(ai_gateway_request_duration_milliseconds_sum{_f}[5m])) '
                    f'/ clamp_min(sum(rate(ai_gateway_request_duration_milliseconds_count{_f}[5m])), 1e-9)',
                    "Avg latency", "A",
                )],
                unit="ms", grid=_grid(0, 0, 24, 8),
            ),
            timeseries_panel(
                "Request Latency — p50 / p95 / p99",
                [
                    _prom_target(
                        f'histogram_quantile(0.50, sum by (le) (rate(ai_gateway_request_duration_milliseconds_bucket{_f}[5m])))',
                        "p50", "A",
                    ),
                    _prom_target(
                        f'histogram_quantile(0.95, sum by (le) (rate(ai_gateway_request_duration_milliseconds_bucket{_f}[5m])))',
                        "p95", "B",
                    ),
                    _prom_target(
                        f'histogram_quantile(0.99, sum by (le) (rate(ai_gateway_request_duration_milliseconds_bucket{_f}[5m])))',
                        "p99", "C",
                    ),
                ],
                unit="ms", grid=_grid(0, 8, 24, 8),
            ),
            heatmap_panel(
                "Latency distribution heatmap",
                f'sum by (le) (rate(ai_gateway_request_duration_milliseconds_bucket{_f}[5m]))',
                unit="ms", grid=_grid(0, 16, 12, 8),
            ),
            timeseries_panel(
                "Latency phase breakdown",
                [
                    _loki_target(
                        f'avg(avg_over_time({_tele} | unwrap queue_wait_ms [5m]))',
                        "Queue wait", "A",
                    ),
                    _loki_target(
                        f'avg(avg_over_time({_tele} | model_inference_ms > 0 | unwrap model_inference_ms [5m]))',
                        "Model inference", "B",
                    ),
                    _loki_target(
                        f'avg(avg_over_time({_tele} | stream_response_ms > 0 | unwrap stream_response_ms [5m]))',
                        "Stream response", "C",
                    ),
                ],
                unit="ms", stacking="normal", fill_opacity=40,
                grid=_grid(12, 16, 12, 8), datasource=DS_LOKI,
            ),
        ], False, "End-to-end latency, percentile breakdown, distribution, and phase split"),
        ("Queue", [
            timeseries_panel(
                "Queue delays",
                [
                    _loki_target(
                        f'avg(avg_over_time({_tele} | unwrap queue_wait_ms [5m]))',
                        "Avg queue delay", "A",
                    ),
                    _loki_target(
                        f'avg by (model_name) (avg_over_time({_tele} | unwrap queue_wait_ms [5m]))',
                        "{{model_name}}", "B",
                    ),
                ],
                unit="ms", grid=_grid(0, 0, 24, 8),
                datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Queue wait p95",
                [_loki_target(
                    f'max(quantile_over_time(0.95, {_tele} | unwrap queue_wait_ms [5m]))',
                    "p95 queue wait", "A",
                )],
                unit="ms", grid=_grid(0, 8, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Time to first token",
                [
                    _loki_target(
                        f'avg(avg_over_time({_tele} | first_token_ms > 0 | unwrap first_token_ms [5m]))',
                        "Avg TTFT", "A",
                    ),
                    _loki_target(
                        f'max(quantile_over_time(0.95, {_tele} | first_token_ms > 0 | unwrap first_token_ms [5m]))',
                        "p95 TTFT", "B",
                    ),
                ],
                unit="ms", grid=_grid(12, 8, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "Average, p95 queue wait, and time-to-first-token"),
        ("SLA", [
            timeseries_panel(
                "SLA breaches by tier (5m)",
                [_loki_target(
                    f'sum by (sla_tier) (count_over_time({_tele} | sla_breached="true" [5m])) or vector(0)',
                    "{{sla_tier}}", "A",
                )],
                unit="short", grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Latency vs SLA target",
                [
                    _loki_target(
                        f'avg(avg_over_time({_tele} | unwrap latency_ms [5m]))',
                        "Avg latency", "A",
                    ),
                    _loki_target(
                        f'avg(avg_over_time({_tele} | unwrap sla_target_ms [5m]))',
                        "Avg SLA target", "B",
                    ),
                ],
                unit="ms", grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "SLA breach counts by tier and latency vs target"),
    ])

    return dashboard(
        uid="ai-telemetry-latency",
        title="2. Network observability",
        description="End-to-end response latency, percentile breakdown, and queue delays.",
        tags=["ai-telemetry", "latency", "network"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   4 — Cost & Usage Metrics
# ===========================================================================

def build_d4() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D8)
    _f = F.prom
    _f_completion = _token_selector("completion", F)
    _f_prompt = _token_selector("prompt", F)
    _req_rate = f'clamp_min(sum(rate(ai_gateway_request_count_total{_f}[5m])), 1e-9)'
    _tok_per_req = f'sum(rate(ai_gateway_request_token_total{_f}[5m])) / {_req_rate}'
    _prompt_per_req = f'sum(rate(ai_gateway_request_token_total{_f_prompt}[5m])) / {_req_rate}'
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    headline = [
        stat_panel(
            "Output tokens",
            f'sum(increase(ai_gateway_request_token_total{_f_completion}[$__range]))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(0, 0, 6, 5),
        ),
        stat_panel(
            "Avg tokens / request",
            _tok_per_req,
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(6, 0, 6, 5),
        ),
        stat_panel(
            "Context fill",
            f'avg(avg_over_time({_tele} | unwrap context_window_utilization_pct [5m]))',
            unit="percent", decimals=1,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 60},
                {"color": "red", "value": 85},
            ],
            color_mode="value",
            grid=_grid(12, 0, 6, 5),
            datasource=DS_LOKI,
        ),
        stat_panel(
            "Errors (5m)",
            f'sum(increase(ai_gateway_exception_count_total{_f}[5m])) or on() vector(0)',
            unit="short", decimals=0,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 3},
                {"color": "red", "value": 10},
            ],
            color_mode="value",
            grid=_grid(18, 0, 6, 5),
        ),
    ]

    panels = build_dashboard_panels(headline, [
        ("Cost", [
            timeseries_panel(
                "Cost per request",
                [
                    # Prometheus (not Loki unwrap): cost ÷ request count is an exact
                    # aggregate over the metrics pipeline — no sampling, no
                    # structured-metadata string parsing. Matches "Cost per user/session".
                    _prom_target(
                        f'sum(increase(ai_gateway_request_cost_USD_total{_f}[5m])) '
                        f'/ clamp_min(sum(increase(ai_gateway_request_count_total{_f}[5m])), 1e-9)',
                        "Avg cost / request", "A",
                    ),
                    _prom_target(
                        f'sum by (model_name)(increase(ai_gateway_request_cost_USD_total{_f}[5m])) '
                        f'/ clamp_min(sum by (model_name)(increase(ai_gateway_request_count_total{_f}[5m])), 1e-9)',
                        "{{model_name}}", "B",
                    ),
                ],
                unit="currencyUSD", grid=_grid(0, 0, 12, 8),
            ),
            timeseries_panel(
                "Cost per user/session",
                [
                    _prom_target(
                        f'sum(increase(ai_gateway_request_cost_USD_total{_f}[5m])) '
                        f'/ clamp_min(sum(increase(ai_gateway_request_count_total{_f}[5m])), 1e-9)',
                        "Avg cost / request", "A",
                    ),
                ],
                unit="currencyUSD", grid=_grid(12, 0, 12, 8),
            ),
            timeseries_panel(
                "Daily/monthly spend",
                [
                    _prom_target(
                        f'sum(increase(ai_gateway_request_cost_USD_total{_f}[24h]))',
                        "Daily spend (24h)", "A",
                    ),
                    _prom_target(
                        f'sum(increase(ai_gateway_request_cost_USD_total{_f}[30d]))',
                        "Monthly spend (30d)", "B",
                    ),
                ],
                unit="currencyUSD", grid=_grid(0, 8, 24, 8),
            ),
        ], False, "Per-request cost and daily/monthly spend totals"),
        ("Cost breakdown", [
            piechart_panel(
                "Cost by department",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (department) (increase(ai_gateway_request_cost_USD_total{_f}[24h])))',
                  "legendFormat": "{{department}}", "refId": "A", "instant": True}],
                grid=_grid(0, 0, 12, 8),
            ),
            barchart_panel(
                "Model-wise cost breakdown",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (model_name) (increase(ai_gateway_request_cost_USD_total{_f}[24h])))',
                  "legendFormat": "{{model_name}}", "refId": "A", "instant": True}],
                unit="currencyUSD", grid=_grid(12, 0, 12, 8),
            ),
        ], True, "Department and model cost distribution"),
        ("Savings", [
            timeseries_panel(
                "Cache hit savings",
                [
                    _loki_target(
                        f'sum(sum_over_time({_tele} | unwrap cache_savings_usd [5m]))',
                        "Cache savings USD", "A",
                    ),
                    _loki_target(
                        f'sum by (model_name) (sum_over_time({_tele} | unwrap cache_savings_usd [5m]))',
                        "{{model_name}}", "B",
                    ),
                ],
                unit="currencyUSD", grid=_grid(0, 0, 24, 8),
                datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Cache hit rate (5m)",
                [_loki_target(
                    _loki_ratio(
                        f'sum(count_over_time({_tele} | cache_hit="true" [5m]))',
                        f'sum(count_over_time({_tele} [5m]))',
                    ),
                    "Cache hit %", "A",
                )],
                unit="percent", decimals=1, axis_soft_max=100,
                grid=_grid(0, 8, 12, 8), datasource=DS_LOKI,
            ),
            stat_panel(
                "Cumulative cache savings (24h)",
                f'sum(sum_over_time({_tele} | unwrap cache_savings_usd [24h]))',
                unit="currencyUSD", decimals=2,
                thresholds=[{"color": "green", "value": None}],
                color_mode="value",
                grid=_grid(12, 8, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "USD saved from cache hits and cache hit rate"),
        ("Output throughput", [
            timeseries_panel(
                "Output Token Count",
                [_prom_target(
                    f'sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[5m]))',
                    "{{model_name}}", "A",
                )],
                unit="tps", decimals=1,
                stacking="normal", fill_opacity=50,
                legend_display_mode="table",
                legend_calcs=["lastNotNull", "mean"],
                grid=_grid(0, 0, 16, 8),
            ),
            bargauge_panel(
                "Output tokens by model (now)",
                f'sort_desc(sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[5m])))',
                unit="tps",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(16, 0, 8, 8),
            ),
        ], False, "Token generation rate by model"),
        ("Request composition", [
            piechart_panel(
                "Token type mix",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (token_type) (increase(ai_gateway_request_token_total{_f}[1h])))',
                  "legendFormat": "{{token_type}}", "refId": "A", "instant": True}],
                pie_type="donut",
                grid=_grid(0, 0, 8, 8),
            ),
            timeseries_panel(
                "Total tokens per request",
                [_prom_target(_tok_per_req, "Avg tokens / request", "A")],
                unit="short", decimals=0,
                fill_opacity=25,
                grid=_grid(8, 0, 8, 8),
            ),
            bargauge_panel(
                "Prompt size by model",
                f'sort_desc(sum by (model_name) (rate(ai_gateway_request_token_total{_f_prompt}[5m])) '
                f'/ clamp_min(sum by (model_name) (rate(ai_gateway_request_count_total{_f}[5m])), 1e-9))',
                unit="short",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(16, 0, 8, 8),
            ),
        ], True, "Prompt vs completion token mix"),
        ("Context window", [
            gauge_panel(
                "Context Window Utilization (%)",
                f'avg(avg_over_time({_tele} | unwrap context_window_utilization_pct [5m]))',
                unit="percent", min_val=0, max_val=100,
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 60},
                    {"color": "red", "value": 85},
                ],
                grid=_grid(0, 0, 6, 8),
                datasource=DS_LOKI,
            ),
            bargauge_panel(
                "Context fill by model",
                f'sort_desc(avg by (model_name) (avg_over_time({_tele} | unwrap context_window_utilization_pct [5m])))',
                unit="percent",
                legend_format="{{model_name}}",
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 60},
                    {"color": "red", "value": 85},
                ],
                grid=_grid(6, 0, 10, 8),
                datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Prompt size trend",
                [_prom_target(_prompt_per_req, "Avg prompt tokens / request", "A")],
                unit="short", decimals=0,
                fill_opacity=30,
                grid=_grid(16, 0, 8, 8),
            ),
        ], True, "Context window fill and prompt size trends"),
        ("Streaming throughput & errors", [
            barchart_panel(
                "Errors by type (5m)",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (error_type) (increase(ai_gateway_exception_count_total{_f}[5m])))',
                  "legendFormat": "{{error_type}}", "refId": "A",
                  "instant": True, "queryType": "instant"}],
                unit="short", grid=_grid(0, 0, 24, 8),
            ),
            timeseries_panel(
                "Streaming tokens/sec — avg & p95",
                [
                    _loki_target(
                        f'avg(avg_over_time({_tele} | tokens_per_second > 0 | unwrap tokens_per_second [5m]))',
                        "Avg tokens/s", "A",
                    ),
                    _loki_target(
                        f'max(quantile_over_time(0.95, {_tele} | tokens_per_second > 0 | unwrap tokens_per_second [5m]))',
                        "p95 tokens/s", "B",
                    ),
                ],
                unit="short", decimals=0, grid=_grid(0, 8, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Streaming share of requests (5m)",
                [_loki_target(
                    _loki_ratio(
                        f'sum(count_over_time({_tele} | streaming="true" [5m]))',
                        f'sum(count_over_time({_tele} [5m]))',
                    ),
                    "Streaming %", "A",
                )],
                unit="percent", decimals=1, axis_soft_max=100,
                grid=_grid(12, 8, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "Error spikes, streaming throughput, and streaming share"),
    ])

    return dashboard(
        uid="ai-telemetry-cost",
        title="6. Cost & Usage Observability",
        description="Cost per request, spend totals, token usage, context window fill, and cache savings.",
        tags=["ai-telemetry", "cost", "usage", "tokens"],
        panels=panels,
        refresh="15s",
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   5 — Model Quality Metrics
# ===========================================================================

def build_d5() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D5)
    _eval = f'{_LOKI_STREAM} event_type="eval_result" {F.loki}'
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'
    # % of judged responses flagged as hallucinating (faithfulness < 5 on 0–10 scale).
    _hallucination_rate = _loki_ratio(
        f'sum(count_over_time({_eval} | faithfulness < 5 [1h]))',
        f'sum(count_over_time({_eval} [1h]))',
    )
    # Judge scores are 0–10; factual accuracy is shown as 0–100 %.
    _factual_accuracy = f'avg(avg_over_time({_eval} | unwrap faithfulness [1h])) * 10'
    _relevance_score = f'avg(avg_over_time({_eval} | unwrap relevance [1h]))'
    _groundedness_score = f'avg(avg_over_time({_eval} | unwrap groundedness [1h]))'

    _score_th = [
        {"color": "red", "value": None},
        {"color": "yellow", "value": 60},
        {"color": "green", "value": 80},
    ]
    _halluc_th = [
        {"color": "green", "value": None},
        {"color": "yellow", "value": 10},
        {"color": "red", "value": 20},
    ]

    _f = F.prom
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    headline = [
        stat_panel(
            "Hallucination rate",
            _hallucination_rate,
            unit="percent", decimals=1,
            thresholds=_halluc_th,
            grid=_grid(0, 0, 6, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Factual accuracy",
            _factual_accuracy,
            unit="percent", decimals=1,
            thresholds=_score_th,
            grid=_grid(6, 0, 6, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Relevance score",
            _relevance_score,
            unit="short", decimals=1,
            thresholds=[{"color":"red","value":None},{"color":"yellow","value":6},{"color":"green","value":8}],
            grid=_grid(12, 0, 6, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Groundedness score",
            _groundedness_score,
            unit="short", decimals=1,
            thresholds=[{"color":"red","value":None},{"color":"yellow","value":6},{"color":"green","value":8}],
            grid=_grid(18, 0, 6, 4), datasource=DS_LOKI,
        ),
    ]

    _stream = f'{_tele} | stream_response_ms > 0'
    _stream_tps = f'{_tele} | tokens_per_second > 0'
    _f_completion = _token_selector("completion", F)

    panels = build_dashboard_panels(headline, [
        ("Model latency", [
            timeseries_panel(
                "Model Specific Latency",
                [_prom_target(
                    f'histogram_quantile(0.95, sum by (le, model_name) '
                    f'(rate(ai_gateway_request_duration_milliseconds_bucket{_f}[5m])))',
                    "{{model_name}}", "A",
                )],
                unit="ms", grid=_grid(0, 0, 12, 8),
            ),
            timeseries_panel(
                "First token latency (Model Based)",
                [_loki_target(
                    f'avg by (model_name) (avg_over_time({_tele} '
                    f'| first_token_ms > 0 | unwrap first_token_ms [5m]))',
                    "{{model_name}}", "A",
                )],
                unit="ms", grid=_grid(12, 0, 12, 8),
                datasource=DS_LOKI,
            ),
        ], False, "Per-model p95 and first-token latency"),
        ("Quality trends", [
            timeseries_panel(
                "Hallucination rate over time",
                [_loki_target(
                    _loki_ratio(
                        f'sum(count_over_time({_eval} | faithfulness < 5 [5m]))',
                        f'sum(count_over_time({_eval} [5m]))',
                    ),
                    "Hallucination %", "A",
                )],
                unit="percent", decimals=1, axis_soft_max=30,
                grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Factual accuracy",
                [_loki_target(f'{_factual_accuracy}', "Factual accuracy %", "A")],
                unit="percent", decimals=1,
                grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Relevance score",
                [
                    _loki_target(f'{_relevance_score}', "Relevance (0–10)", "A"),
                    _loki_target(
                        f'avg by (model_name) (avg_over_time({_eval} | unwrap relevance [1h]))',
                        "{{model_name}}", "B",
                    ),
                ],
                unit="short", decimals=1,
                grid=_grid(0, 8, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Groundedness score",
                [
                    _loki_target(f'{_groundedness_score}', "Groundedness (0–10)", "A"),
                    _loki_target(
                        f'avg by (model_name) (avg_over_time({_eval} | unwrap groundedness [1h]))',
                        "{{model_name}}", "B",
                    ),
                ],
                unit="short", decimals=1,
                grid=_grid(12, 8, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Factual accuracy by model",
                [_loki_target(
                    f'avg by (model_name) (avg_over_time({_eval} | unwrap faithfulness [1h])) * 10',
                    "{{model_name}}", "A",
                )],
                unit="percent", decimals=1,
                grid=_grid(0, 16, 24, 8), datasource=DS_LOKI,
            ),
        ], False, "Judge score trends over time and per-model quality"),
        ("Evaluation Ops", [
            stat_panel(
                "Evaluation Coverage",
                _loki_ratio(
                    f'sum(count_over_time({_eval} [1h]))',
                    f'sum(count_over_time({_tele} [1h]))',
                ),
                unit="percent", decimals=2,
                thresholds=[{"color": "blue", "value": None}],
                grid=_grid(0, 0, 6, 4), datasource=DS_LOKI,
            ),
            stat_panel(
                "Evaluator Errors (24h)",
                f'sum(count_over_time({_eval} |~ "mock_judge_timeout" [24h])) or on() vector(0)',
                unit="short", decimals=0,
                thresholds=[{"color":"green","value":None},{"color":"yellow","value":1},{"color":"red","value":10}],
                grid=_grid(6, 0, 6, 4), datasource=DS_LOKI,
            ),
            logs_panel(
                "Low-Quality Responses (hallucination flagged)",
                f'{_eval} | faithfulness < 5',
                grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Evaluation latency (judge)",
                [
                    _loki_target(
                        f'avg(avg_over_time({_eval} | unwrap eval_latency_ms [5m]))',
                        "Avg eval latency", "A",
                    ),
                    _loki_target(
                        f'max(quantile_over_time(0.95, {_eval} | unwrap eval_latency_ms [5m]))',
                        "p95 eval latency", "B",
                    ),
                ],
                unit="ms", grid=_grid(0, 4, 12, 8), datasource=DS_LOKI,
            ),
            stat_panel(
                "Eval pass-rate (faithfulness ≥ 5)",
                _loki_ratio(
                    f'sum(count_over_time({_eval} | faithfulness >= 5 [1h]))',
                    f'sum(count_over_time({_eval} [1h]))',
                ),
                unit="percent", decimals=1,
                thresholds=[{"color": "red", "value": None}, {"color": "yellow", "value": 80}, {"color": "green", "value": 90}],
                grid=_grid(12, 8, 12, 4), datasource=DS_LOKI,
            ),
        ], True, "Coverage, evaluator health, judge latency, and flagged responses"),
        ("Streaming performance", [
            timeseries_panel(
                "Live token generation rate",
                [_prom_target(
                    f'sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[1m]))',
                    "{{model_name}}", "A",
                )],
                unit="tps", decimals=1,
                stacking="normal", fill_opacity=45,
                legend_display_mode="table",
                legend_calcs=["lastNotNull"],
                grid=_grid(0, 0, 12, 8),
            ),
            timeseries_panel(
                "Streaming response latency",
                [_loki_target(
                    f'avg by (model_name) (avg_over_time({_stream} | unwrap stream_response_ms [5m]))',
                    "{{model_name}}", "A",
                )],
                unit="ms", decimals=0,
                grid=_grid(12, 0, 12, 8),
                datasource=DS_LOKI,
            ),
        ], True, "Live token generation and streaming response latency"),
        ("Streaming throughput", [
            bargauge_panel(
                "Streaming tokens/sec",
                f'sort_desc(avg by (model_name) (avg_over_time({_stream_tps} | unwrap tokens_per_second [5m])))',
                unit="tps",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(0, 0, 10, 7),
                datasource=DS_LOKI,
            ),
            stat_panel(
                "Avg stream tokens/s",
                f'avg(avg_over_time({_stream_tps} | unwrap tokens_per_second [5m]))',
                unit="tps", decimals=1,
                thresholds=[{"color": "blue", "value": None}],
                color_mode="value",
                grid=_grid(10, 0, 5, 7),
                datasource=DS_LOKI,
            ),
        ], True, "Per-model streaming tokens per second"),
    ])

    return dashboard(
        uid="ai-telemetry-quality",
        title="3. AI observability",
        description="Model-specific latency, first-token latency, quality scores, and streaming performance.",
        tags=["ai-telemetry", "quality", "evaluation", "ai"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   6 — Safety & Security Metrics
# ===========================================================================

def build_d6() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D6)
    _plog = f'{_LOKI_STREAM} event_type="prompt_log_event" {F.loki}'
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    _pii_rate = _loki_ratio(
        f'sum(count_over_time({_plog} | pii_detected="true" [24h]))',
        f'sum(count_over_time({_plog} [24h]))',
    )
    _toxicity_score = f'avg(avg_over_time({_plog} | unwrap toxicity_score [24h])) * 100'

    _safety_th = [
        {"color": "green", "value": None},
        {"color": "yellow", "value": 30},
        {"color": "red", "value": 60},
    ]

    headline = [
        stat_panel(
            "Toxicity score",
            _toxicity_score,
            unit="percent", decimals=1,
            thresholds=_safety_th,
            grid=_grid(0, 0, 5, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "PII detection rate",
            _pii_rate,
            unit="percent", decimals=1,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 15}],
            grid=_grid(5, 0, 5, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Prompt injection attempts",
            f'sum(count_over_time({_plog} | prompt_injection_detected="true" [24h])) or vector(0)',
            unit="short", decimals=0,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 25}],
            grid=_grid(10, 0, 5, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Jailbreak attempts",
            f'sum(count_over_time({_plog} | jailbreak_attempt="true" [24h])) or vector(0)',
            unit="short", decimals=0,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 3}, {"color": "red", "value": 15}],
            grid=_grid(15, 0, 5, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Compliance violations",
            f'sum(count_over_time({_plog} | compliance_violation="true" [24h])) or vector(0)',
            unit="short", decimals=0,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 20}],
            grid=_grid(20, 0, 4, 4), datasource=DS_LOKI,
        ),
    ]

    panels = build_dashboard_panels(headline, [
        ("Safety Trends", [
            timeseries_panel(
                "Toxicity score",
                [_loki_target(f'avg(avg_over_time({_plog} | unwrap toxicity_score [5m])) * 100', "Toxicity %")],
                unit="percent", decimals=1, axis_soft_max=100,
                grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "PII detection rate",
                [
                    _loki_target(_loki_ratio(
                        f'sum(count_over_time({_plog} | pii_detected="true" [5m]))',
                        f'sum(count_over_time({_plog} [5m]))',
                    ), "PII %"),
                ],
                unit="percent", grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Prompt injection attempts / min",
                [_loki_target(
                    f'sum(count_over_time({_plog} | prompt_injection_detected="true" [1m])) or vector(0)',
                    "Injections/min",
                )],
                unit="short", grid=_grid(0, 8, 8, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Jailbreak attempts / min",
                [_loki_target(
                    f'sum(count_over_time({_plog} | jailbreak_attempt="true" [1m])) or vector(0)',
                    "Jailbreaks/min",
                )],
                unit="short", grid=_grid(8, 8, 8, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Compliance violations / min",
                [_loki_target(
                    f'sum(count_over_time({_plog} | compliance_violation="true" [1m])) or vector(0)',
                    "Violations/min",
                )],
                unit="short", grid=_grid(16, 8, 8, 8), datasource=DS_LOKI,
            ),
        ], True, "Time-series safety signals and attack rates"),
        ("PII & Data Classification", [
            stat_panel("PII Events Today",
                       f'sum(count_over_time({_plog} | pii_detected="true" [24h])) or vector(0)',
                       unit="short", decimals=0,
                       thresholds=[{"color":"green","value":None},{"color":"yellow","value":10},{"color":"red","value":50}],
                       grid=_grid(0, 0, 4, 4), datasource=DS_LOKI),
            stat_panel("PHI Requests Today",
                       f'sum(count_over_time({_tele} | data_classification="phi" [24h])) or vector(0)',
                       unit="short", decimals=0,
                       thresholds=[{"color":"blue","value":None}],
                       grid=_grid(4, 0, 4, 4), datasource=DS_LOKI),
            stat_panel("PII Requests Today",
                       f'sum(count_over_time({_tele} | data_classification="pii" [24h])) or vector(0)',
                       unit="short", decimals=0,
                       thresholds=[{"color":"blue","value":None}],
                       grid=_grid(8, 0, 4, 4), datasource=DS_LOKI),
            stat_panel("Unique Prompt Hashes (24h)",
                       f'count(sum by (prompt_hash) (count_over_time({_plog} [24h])))',
                       unit="short", decimals=0,
                       thresholds=[{"color":"blue","value":None}],
                       grid=_grid(12, 0, 4, 4), datasource=DS_LOKI),
            piechart_panel(
                "Data Classification Distribution",
                [_loki_instant_target(f'sum by (data_classification) (count_over_time({_tele} [1h]))', "{{data_classification}}")],
                grid=_grid(16, 0, 8, 4), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "PII Events by Department",
                [_loki_target(f'sum by (department) (count_over_time({_plog} | pii_detected="true" [5m])) or vector(0)', "{{department}}")],
                unit="short", grid=_grid(0, 4, 12, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "PHI + PII Volume by Department (last 1h)",
                [_loki_instant_target(f'sum by (department) (count_over_time({_tele} | data_classification=~"phi|pii" [1h]))', "{{department}}")],
                unit="short", grid=_grid(12, 4, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "PII/PHI volume and data classification mix"),
        ("Prompt Audit Log", [
            piechart_panel(
                "Guardrail actions (1h)",
                [_loki_instant_target(
                    f'sum by (guardrail_action) (count_over_time({_tele} | guardrail_action != "" [1h]))',
                    "{{guardrail_action}}",
                )],
                pie_type="donut",
                grid=_grid(0, 0, 8, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Injection vs jailbreak (per min)",
                [
                    _loki_target(
                        f'sum(count_over_time({_plog} | prompt_injection_detected="true" [1m])) or vector(0)',
                        "Injections/min", "A",
                    ),
                    _loki_target(
                        f'sum(count_over_time({_plog} | jailbreak_attempt="true" [1m])) or vector(0)',
                        "Jailbreaks/min", "B",
                    ),
                ],
                unit="short", grid=_grid(8, 0, 16, 8), datasource=DS_LOKI,
            ),
            logs_panel(
                "Prompt Log Events (PII-scrubbed)",
                f'{_plog}',
                grid=_grid(0, 8, 24, 10), datasource=DS_LOKI,
            ),
        ], True, "Guardrail outcomes, attack trend, and PII-scrubbed audit stream"),
        ("High-Risk Request Table", [
            table_panel(
                "PHI / PII Requests with Trace Links",
                [{"datasource": DS_LOKI,
                  "expr": f'{_tele} | data_classification=~"phi|pii" | line_format "{{.request_id}} {{.department}} {{.model_name}} {{.trace_id}}"',
                  "refId": "A"}],
                grid=_grid(0, 0, 24, 8), datasource=DS_LOKI,
            ),
            logs_panel(
                "Safety incidents (injection, jailbreak, compliance)",
                f'{_plog} | prompt_injection_detected="true" or jailbreak_attempt="true" or compliance_violation="true"',
                grid=_grid(0, 8, 24, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Toxicity p95 by department",
                [_loki_target(
                    f'max by (department) (quantile_over_time(0.95, {_plog} | unwrap toxicity_score [5m])) * 100',
                    "{{department}}", "A",
                )],
                unit="percent", decimals=1, axis_soft_max=100,
                grid=_grid(0, 16, 24, 8), datasource=DS_LOKI,
            ),
        ], True, "High-risk PHI/PII requests, safety incidents, and toxicity by department"),
    ])

    return dashboard(
        uid="ai-telemetry-safety",
        title="7. Safety and security",
        description="Toxicity score, PII detection rate, prompt injection, jailbreak, and compliance violations.",
        tags=["ai-telemetry", "safety", "security", "pii", "compliance"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   7 — Infrastructure Metrics
# ===========================================================================

def build_d7() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D7)
    _ns = 'namespace="ai-gateway-ns"'
    _gw = 'namespace="ai-gateway-ns", container="ai-gateway"'
    _f = F.prom
    _f_completion = _token_selector("completion", F)
    _f_prompt = _token_selector("prompt", F)
    _cpu_util = (
        f'sum(rate(container_cpu_usage_seconds_total{{{_gw}}}[5m])) '
        f'/ clamp_min(sum(kube_pod_status_phase{{{_ns}, phase="Running"}}), 1) * 100'
    )
    _model_throughput = f'sum(rate(ai_gateway_request_token_total{_f_completion}[5m]))'
    _oom_failures = (
        f'sum(increase(kube_pod_container_oom_killed_total{{{_ns}}}[24h])) or vector(0)'
    )
    _pod_health = (
        f'kube_deployment_status_replicas_available{{{_ns}}} '
        f'/ clamp_min(kube_deployment_spec_replicas{{{_ns}}}, 1) * 100'
    )
    _scaling_events = (
        f'sum(increase(kube_horizontalpodautoscaler_scaling_events_total{{{_ns}}}[24h])) '
        f'or vector(0)'
    )
    _api_error_rate = (
        f'sum(rate(ai_gateway_request_count_total{F.prom_error}[5m])) '
        f'/ clamp_min(sum(rate(ai_gateway_request_count_total{_f}[5m])), 1e-9) * 100'
    )

    headline = [
        stat_panel(
            "CPU utilization",
            _cpu_util,
            unit="percent", decimals=1,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 90}],
            grid=_grid(0, 0, 4, 4),
        ),
        stat_panel(
            "Model throughput",
            _model_throughput,
            unit="tps", decimals=1,
            thresholds=[{"color": "blue", "value": None}],
            grid=_grid(4, 0, 4, 4),
        ),
        stat_panel(
            "OOM failures",
            _oom_failures,
            unit="short", decimals=0,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 3}],
            grid=_grid(8, 0, 4, 4),
        ),
        stat_panel(
            "Pod/container health",
            _pod_health,
            unit="percent", decimals=1,
            thresholds=[{"color": "red", "value": None}, {"color": "yellow", "value": 80}, {"color": "green", "value": 95}],
            grid=_grid(12, 0, 4, 4),
        ),
        stat_panel(
            "Auto-scaling events",
            _scaling_events,
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            grid=_grid(16, 0, 4, 4),
        ),
        stat_panel(
            "API error rate",
            _api_error_rate,
            unit="percent", decimals=2,
            thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 2}, {"color": "red", "value": 5}],
            grid=_grid(20, 0, 4, 4),
        ),
    ]

    panels = build_dashboard_panels(headline, [
        ("Infrastructure Trends", [
        timeseries_panel(
            "CPU utilization",
            [_prom_target(_cpu_util, "CPU % per pod (avg cores)", "A")],
            unit="percent", decimals=1, grid=_grid(0, 0, 12, 8),
        ),
        timeseries_panel(
            "Model throughput",
            [
                _prom_target(_model_throughput, "Completion tokens/s", "A"),
                _prom_target(
                    f'sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[5m]))',
                    "{{model_name}}", "B",
                ),
            ],
            unit="tps", grid=_grid(12, 0, 12, 8),
        ),
        timeseries_panel(
            "OOM failures",
            [_prom_target(
                f'sum(increase(kube_pod_container_oom_killed_total{{{_ns}}}[1h])) or vector(0)',
                "OOM events / h", "A",
            )],
            unit="short", grid=_grid(0, 8, 8, 8),
        ),
        timeseries_panel(
            "Pod/container health",
            [
                _prom_target(_pod_health, "Available %", "A"),
                _prom_target(
                    f'sum(kube_pod_status_phase{{{_ns}, phase="Running"}})',
                    "Running pods", "B",
                ),
            ],
            unit="percent", decimals=1, grid=_grid(8, 8, 8, 8),
        ),
        timeseries_panel(
            "Auto-scaling events",
            _prom_targets(
                (f'sum(increase(kube_horizontalpodautoscaler_scaling_events_total{{{_ns}, direction="up"}}[1h])) or vector(0)', "scale up"),
                (f'sum(increase(kube_horizontalpodautoscaler_scaling_events_total{{{_ns}, direction="down"}}[1h])) or vector(0)', "scale down"),
            ),
            unit="short", grid=_grid(16, 8, 8, 8),
        ),
        timeseries_panel(
            "API error rate",
            [
                _prom_target(_api_error_rate, "Error %", "A"),
                _prom_target(
                    f'sum by (error_type) (rate(ai_gateway_exception_count_total{_f}[5m]))',
                    "{{error_type}}", "B",
                ),
            ],
            unit="percent", decimals=2, grid=_grid(0, 16, 24, 8),
        ),
        ], True, "CPU, throughput, pod health, and error trends"),
        ("HPA Scaling", [
        timeseries_panel(
            "HPA Current vs Desired Replicas",
            _prom_targets(
                ('kube_horizontalpodautoscaler_status_current_replicas{namespace="ai-gateway-ns"}', "current"),
                ('kube_horizontalpodautoscaler_status_desired_replicas{namespace="ai-gateway-ns"}', "desired"),
                ('kube_horizontalpodautoscaler_spec_min_replicas{namespace="ai-gateway-ns"}', "min"),
                ('kube_horizontalpodautoscaler_spec_max_replicas{namespace="ai-gateway-ns"}', "max"),
            ),
            unit="short", grid=_grid(0, 0, 12, 8),
        ),
        timeseries_panel(
            "Pod Restart Rate",
            [_prom_target(
                'sum by (pod) (rate(kube_pod_container_status_restarts_total{namespace="ai-gateway-ns"}[15m])) '
                'or on() label_replace(vector(0), "pod", "none", "", "")',
                "{{pod}}",
            )],
            unit="ops", grid=_grid(12, 0, 12, 8),
        ),
        timeseries_panel(
            "Scaling Pressure (desired − current)",
            [_prom_target(
                'kube_horizontalpodautoscaler_status_desired_replicas{namespace="ai-gateway-ns"} '
                '- kube_horizontalpodautoscaler_status_current_replicas{namespace="ai-gateway-ns"}',
                "desired − current", "A",
            )],
            unit="short", grid=_grid(0, 8, 12, 8),
        ),
        stat_panel(
            "Current Replicas",
            'max(kube_horizontalpodautoscaler_status_current_replicas{namespace="ai-gateway-ns"})',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(12, 8, 12, 8),
        ),
        ], True, "Replica counts, scaling pressure, and pod restart activity"),
        ("Node Pressure", [
        gauge_panel(
            "Node memory used %",
            '(1 - node_memory_MemAvailable_bytes / clamp_min(node_memory_MemTotal_bytes, 1)) * 100',
            unit="percent", min_val=0, max_val=100,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 70},
                {"color": "red", "value": 90},
            ],
            grid=_grid(0, 0, 6, 8),
        ),
        gauge_panel(
            "Node filesystem used %",
            '(1 - node_filesystem_avail_bytes / clamp_min(node_filesystem_size_bytes, 1)) * 100',
            unit="percent", min_val=0, max_val=100,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 75},
                {"color": "red", "value": 90},
            ],
            grid=_grid(6, 0, 6, 8),
        ),
        timeseries_panel(
            "Node load (1m avg)",
            [_prom_target('node_load1', "load1", "A")],
            unit="short", decimals=2, grid=_grid(12, 0, 12, 8),
        ),
        timeseries_panel(
            "Node CPU busy %",
            [_prom_target(
                'sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) '
                '/ clamp_min(sum(rate(node_cpu_seconds_total[5m])), 1e-9) * 100',
                "CPU busy %", "A",
            )],
            unit="percent", decimals=1, grid=_grid(0, 8, 24, 8),
        ),
        ], True, "Node-level memory, disk, load, and CPU saturation"),
        ("Container Resources", [
        timeseries_panel(
            "Container Memory RSS by Pod",
            [_prom_target('container_memory_rss{namespace="ai-gateway-ns", container="ai-gateway"}', "{{pod}}")],
            unit="bytes", grid=_grid(0, 0, 12, 8),
        ),
        timeseries_panel(
            "Container CPU Usage by Pod",
            [_prom_target('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="ai-gateway-ns", container="ai-gateway"}[2m]))', "{{pod}}")],
            unit="percentunit", grid=_grid(12, 0, 12, 8),
        ),
        timeseries_panel(
            "Node Memory Available Over Time",
            _prom_targets(
                ('node_memory_MemAvailable_bytes', "available"),
                ('node_memory_MemTotal_bytes', "total"),
            ),
            unit="bytes", grid=_grid(0, 8, 12, 8),
        ),
        timeseries_panel(
            "Node CPU Usage (user mode)",
            [_prom_target('rate(node_cpu_seconds_total{mode="user"}[2m]) * 100', "cpu user %")],
            unit="percent", grid=_grid(12, 8, 12, 8),
        ),
        ], True, "Per-pod and node-level resource usage"),
        ("Runner Self-Observability", [
        heatmap_panel(
            "Batch Duration Heatmap",
            'sum by (le) (rate(ai_telemetry_runner_batch_duration_seconds_bucket[2m]))',
            unit="s", grid=_grid(0, 0, 12, 8),
        ),
        timeseries_panel(
            "Kafka Queue Depth",
            [_prom_target('ai_telemetry_runner_kafka_queue_depth', "queue depth")],
            unit="short", grid=_grid(12, 0, 12, 8),
        ),
        timeseries_panel(
            "Runner Event Publish Rate",
            [
                _prom_target(
                    'sum(rate(ai_gateway_request_count_total[5m]))',
                    "events published/s", "A",
                ),
            ],
            unit="ops", grid=_grid(0, 8, 12, 8),
        ),
        timeseries_panel(
            "Batch Duration p99",
            [_prom_target('histogram_quantile(0.99, sum by (le) (rate(ai_telemetry_runner_batch_duration_seconds_bucket[5m])))', "p99 batch duration")],
            unit="s", grid=_grid(12, 8, 12, 8),
        ),
        ], True, "Telemetry runner batching and Kafka queue depth"),
        ("OTel Collector", [
        timeseries_panel(
            "Collector Exporter Queue Size",
            _prom_targets(
                ('otelcol_exporter_queue_size{exporter="otlp/tempo"}',       "tempo queue"),
                ('otelcol_exporter_queue_size{exporter="loki"}',             "loki queue"),
                ('rate(otelcol_exporter_sent_spans[2m])',                     "spans exported/s"),
                ('rate(otelcol_exporter_sent_log_records[2m])',               "logs exported/s"),
            ),
            unit="short", grid=_grid(0, 0, 12, 8),
        ),
        timeseries_panel(
            "Collector Export Throughput & Failures",
            _prom_targets(
                ('rate(otelcol_exporter_sent_spans[2m])',                     "spans/s"),
                ('rate(otelcol_exporter_sent_log_records[2m])',               "logs/s"),
                ('rate(otelcol_exporter_send_failed_spans[5m])',              "failed spans/s"),
                ('rate(otelcol_exporter_send_failed_log_records[5m])',       "failed logs/s"),
            ),
            unit="ops", grid=_grid(12, 0, 12, 8),
        ),
        ], True, "OpenTelemetry collector export queues and failures"),
    ])

    return dashboard(
        uid="ai-telemetry-infra",
        title="1. Infrastructure observability",
        description="CPU utilization, model throughput, OOM failures, pod health, auto-scaling, and API error rates.",
        tags=["ai-telemetry", "infrastructure", "kubernetes", "sre"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   8 — Token & Context Metrics
# ===========================================================================

def build_d8() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D8)
    _f = F.prom
    _f_completion = _token_selector("completion", F)
    _f_prompt = _token_selector("prompt", F)
    _req_rate = f'clamp_min(sum(rate(ai_gateway_request_count_total{_f}[5m])), 1e-9)'
    _tok_per_req = (
        f'sum(rate(ai_gateway_request_token_total{_f}[5m])) / {_req_rate}'
    )
    _prompt_per_req = (
        f'sum(rate(ai_gateway_request_token_total{_f_prompt}[5m])) / {_req_rate}'
    )
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'

    panels = build_dashboard_panels(None, [
        ("Output Throughput", [
            timeseries_panel(
                "Output Token Count",
                [_prom_target(
                    f'sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[5m]))',
                    "{{model_name}}", "A",
                )],
                unit="tps", decimals=1,
                stacking="normal", fill_opacity=50,
                legend_display_mode="table",
                legend_calcs=["lastNotNull", "mean"],
                grid=_grid(0, 0, 16, 8),
            ),
            bargauge_panel(
                "Output tokens by model (now)",
                f'sort_desc(sum by (model_name) (rate(ai_gateway_request_token_total{_f_completion}[5m])))',
                unit="tps",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(16, 0, 8, 8),
            ),
        ], False, "Token generation rate by model"),
        ("Request Composition", [
            piechart_panel(
                "Token type mix",
                [{"datasource": DS_PROMETHEUS,
                  "expr": f'sort_desc(sum by (token_type) (increase(ai_gateway_request_token_total{_f}[1h])))',
                  "legendFormat": "{{token_type}}", "refId": "A", "instant": True}],
                pie_type="donut",
                grid=_grid(0, 0, 8, 8),
            ),
            timeseries_panel(
                "Total tokens per request",
                [_prom_target(_tok_per_req, "Avg tokens / request", "A")],
                unit="short", decimals=0,
                fill_opacity=25,
                grid=_grid(8, 0, 8, 8),
            ),
            bargauge_panel(
                "Prompt size by model",
                f'sort_desc(sum by (model_name) (rate(ai_gateway_request_token_total{_f_prompt}[5m])) '
                f'/ clamp_min(sum by (model_name) (rate(ai_gateway_request_count_total{_f}[5m])), 1e-9))',
                unit="short",
                legend_format="{{model_name}}",
                color_mode="palette",
                grid=_grid(16, 0, 8, 8),
            ),
        ], True, "Prompt vs completion token mix"),
        ("Context Window", [
            gauge_panel(
                "Context Window Utilization (%)",
                f'avg(avg_over_time({_tele} | unwrap context_window_utilization_pct [5m]))',
                unit="percent", min_val=0, max_val=100,
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 60},
                    {"color": "red", "value": 85},
                ],
                grid=_grid(0, 0, 6, 8),
                datasource=DS_LOKI,
            ),
            bargauge_panel(
                "Context fill by model",
                f'sort_desc(avg by (model_name) (avg_over_time({_tele} | unwrap context_window_utilization_pct [5m])))',
                unit="percent",
                legend_format="{{model_name}}",
                thresholds=[
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 60},
                    {"color": "red", "value": 85},
                ],
                grid=_grid(6, 0, 10, 8),
                datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Prompt size trend",
                [_prom_target(_prompt_per_req, "Avg prompt tokens / request", "A")],
                unit="short", decimals=0,
                fill_opacity=30,
                grid=_grid(16, 0, 8, 8),
            ),
        ], True, "Context window fill and prompt size trends"),
    ])

    return dashboard(
        uid="ai-telemetry-tokens",
        title="8. Token & Context Metrics (legacy)",
        description="Legacy token dashboard — primary token/cost panels moved to Cost & Usage and AI observability.",
        tags=["ai-telemetry", "tokens", "context", "streaming"],
        panels=panels,
        refresh="15s",
        variables=F.variables(),
    )


# ===========================================================================
# D A S H B O A R D   9 — User-Level Observability
# ===========================================================================

def build_d9() -> dict:
    global _id_counter; _id_counter = 0

    F = FilterSet(*FILTERS_D9)
    _tele = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki}'
    _tele_by_dept = f'{_LOKI_STREAM} event_type="telemetry_event" {F.loki_except("department")}'
    _login = f'{_LOKI_STREAM} event_type="login_event" {F.login_loki}'

    # LogQL does not support Prometheus clamp_min — use `or on(...) vector(...)` instead.
    _prev_15m_tokens = (
        f"sum(sum_over_time({_tele} | unwrap total_tokens [30m])) "
        f"- sum(sum_over_time({_tele} | unwrap total_tokens [15m]))"
    )
    _spike_pct = (
        f"("
        f"sum(sum_over_time({_tele} | unwrap total_tokens [15m])) "
        f"/ ({_prev_15m_tokens} or vector(1)) - 1) * 100"
    )
    _user_spike_ratio = (
        f"topk(10, "
        f"(sum by (user_id) (sum_over_time({_tele} | unwrap total_tokens [15m]))) "
        f"/ ("
        f"  sum by (user_id) (sum_over_time({_tele} | unwrap total_tokens [30m])) "
        f"  - sum by (user_id) (sum_over_time({_tele} | unwrap total_tokens [15m])) "
        f"  or on(user_id) vector(1)"
        f"))"
    )

    headline = [
        stat_panel(
            "Active users",
            f'count(count by (user_id) (count_over_time({_tele} | user_id != "" [5m])))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(0, 0, 4, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Active sessions",
            f'count(count by (session_id) (count_over_time({_tele} | session_id != "" [5m])))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            color_mode="value",
            grid=_grid(4, 0, 4, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Logins (24h)",
            f'sum(count_over_time({_login} [24h])) or vector(0)',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            grid=_grid(8, 0, 4, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Active users (24h)",
            f'count(count by (user_id) (count_over_time({_tele} | user_id != "" [24h])))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            grid=_grid(12, 0, 4, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "Monthly active users (30d)",
            f'count(count by (user_id) (count_over_time({_login} | user_id != "" [30d])))',
            unit="short", decimals=0,
            thresholds=[{"color": "blue", "value": None}],
            grid=_grid(16, 0, 4, 4), datasource=DS_LOKI,
        ),
        stat_panel(
            "LLM usage spike (15m vs prev 15m)",
            _spike_pct,
            unit="percent", decimals=1,
            thresholds=[
                {"color": "green", "value": None},
                {"color": "yellow", "value": 50},
                {"color": "red", "value": 150},
            ],
            grid=_grid(20, 0, 4, 4), datasource=DS_LOKI,
        ),
    ]

    panels = build_dashboard_panels(headline, [
        ("Login & user growth", [
            timeseries_panel(
                "Login track",
                [_loki_target(
                    f'sum(count_over_time({_login} [5m])) or vector(0)',
                    "Logins / 5m", "A",
                )],
                unit="short", grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            stat_panel(
                "Daily active users (24h)",
                f'count(count by (user_id) (count_over_time({_login} [24h])))',
                unit="short", decimals=0,
                thresholds=[{"color": "blue", "value": None}],
                color_mode="value",
                grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "Active users by department (24h)",
                [_loki_instant_target(
                    f'sort_desc(count by (department) (count by (user_id, department) '
                    f'(count_over_time({_tele_by_dept} | user_id != "" | department != "" [24h]))))',
                    "{{department}}",
                )],
                unit="short",
                orientation="horizontal",
                grid=_grid(0, 8, 24, 8),
                datasource=DS_LOKI,
            ),
        ], False, "Login activity and department-level user counts"),
        ("Token consumption by user", [
            barchart_panel(
                "Top 10 users — tokens (24h)",
                [_loki_instant_target(
                    f'topk(10, sum by (user_id) (sum_over_time({_tele} | unwrap total_tokens [24h])))',
                    "{{user_id}}",
                )],
                unit="short", grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "Top 10 users — token rate (5m)",
                [_loki_instant_target(
                    f'topk(10, sum by (user_id) (sum_over_time({_tele} | unwrap total_tokens [5m])))',
                    "{{user_id}}",
                )],
                unit="short", grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "Top 10 users — cost (24h)",
                [_loki_instant_target(
                    f'topk(10, sum by (user_id) (sum_over_time({_tele} | unwrap cost_usd [24h])))',
                    "{{user_id}}",
                )],
                unit="currencyUSD", grid=_grid(0, 8, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Tokens per session (5m)",
                [_loki_target(
                    f'sum(sum_over_time({_tele} | unwrap total_tokens [5m])) '
                    f'/ (count(count by (session_id) (count_over_time({_tele} | session_id != "" [5m]))) or vector(1))',
                    "Tokens / session", "A",
                )],
                unit="short", grid=_grid(12, 8, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "Heaviest token consumers and per-session token intensity"),
        ("Session-level usage", [
            barchart_panel(
                "Top 10 users — session time (6h)",
                [_loki_instant_target(
                    f'topk(10, sum by (user_id) (sum_over_time({_tele} | unwrap session_time_ms [6h])))',
                    "{{user_id}}",
                )],
                unit="ms", grid=_grid(0, 0, 12, 8), datasource=DS_LOKI,
            ),
            table_panel(
                "Top users by tokens (6h)",
                [_loki_instant_target(
                    f"topk(50, sum by (user_id, department) "
                    f"(sum_over_time({_tele} | unwrap total_tokens [6h])))",
                    "",
                )],
                grid=_grid(12, 0, 12, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "Session time by user (top 10, 5m)",
                [_loki_instant_target(
                    f'topk(10, sum by (user_id) (sum_over_time({_tele} | unwrap session_time_ms [5m])))',
                    "{{user_id}}",
                )],
                unit="ms", grid=_grid(0, 8, 24, 8), datasource=DS_LOKI,
            ),
            stat_panel(
                "Avg turns per session (1h)",
                f'avg(max_over_time({_tele} | unwrap turn_number [1h]))',
                unit="short", decimals=1,
                thresholds=[{"color": "blue", "value": None}],
                color_mode="value",
                grid=_grid(0, 16, 12, 8), datasource=DS_LOKI,
            ),
            timeseries_panel(
                "Session duration p95",
                [_loki_target(
                    f'max(quantile_over_time(0.95, {_tele} | unwrap session_time_ms [5m]))',
                    "p95 session time", "A",
                )],
                unit="ms", grid=_grid(12, 16, 12, 8), datasource=DS_LOKI,
            ),
        ], True, "Session duration, turn depth, and per-user token tables"),
        ("Usage spikes", [
            timeseries_panel(
                "Token volume — spike detector (5m buckets)",
                [
                    _loki_target(
                        f'sum(sum_over_time({_tele} | unwrap total_tokens [5m]))',
                        "Tokens / 5m", "A",
                    ),
                    _loki_target(
                        f'sum(sum_over_time({_tele} | unwrap total_tokens [1h])) / 12',
                        "1h avg per 5m", "B",
                    ),
                ],
                unit="short", grid=_grid(0, 0, 14, 8), datasource=DS_LOKI,
            ),
            barchart_panel(
                "Top 10 users — spike ratio (15m vs prev 15m)",
                [_loki_instant_target(_user_spike_ratio, "{{user_id}}")],
                unit="short", grid=_grid(14, 0, 10, 8), datasource=DS_LOKI,
            ),
            logs_panel(
                "Recent login events",
                f'{_login}',
                grid=_grid(0, 8, 24, 8), datasource=DS_LOKI,
            ),
        ], True, "Sudden usage spikes and recent login audit"),
    ])

    return dashboard(
        uid="ai-telemetry-users",
        title="5. User observability",
        description=(
            "Active users and sessions, login tracking, monthly user growth, "
            "top token consumers, session time, and usage spikes."
        ),
        tags=["ai-telemetry", "users", "sessions", "login"],
        panels=panels,
        variables=F.variables(),
    )


# ===========================================================================
# Generate
# ===========================================================================

if __name__ == "__main__":
    builders = [
        ("01-infra-runner.json",         build_d7),
        ("02-latency-performance.json", build_d3),
        ("03-model-quality.json",        build_d5),
        ("04-executive-overview.json",   build_d1),
        ("05-user-observability.json",   build_d9),
        ("06-token-cost.json",           build_d4),
        ("07-safety-pii.json",           build_d6),
    ]
    print("Generating Grafana dashboards…")
    for fname, builder in builders:
        d = builder()
        _save(fname, d)
    print(f"\nDone — {len(builders)} dashboards written to {OUT_DIR}/")
