"""Panel builders for Users Observability dashboard (Excel-aligned)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from users_observability_metrics import UsersObsMetric, users_metric_tooltip


@dataclass
class UserObsContext:
    tele: str
    tele_by_dept: str
    login: str

    @property
    def dau(self) -> str:
        return f'count(count by (user_id) (count_over_time({self.login} | user_id != "" [1h])))'

    @property
    def wau(self) -> str:
        return f'count(count by (user_id) (count_over_time({self.login} | user_id != "" [6h])))'

    @property
    def mau(self) -> str:
        return f'count(count by (user_id) (count_over_time({self.login} | user_id != "" [6h])))'

    @property
    def eligible(self) -> str:
        return (
            f'sum(max by (department) (max_over_time({self.tele_by_dept} '
            f'| unwrap eligible_user_count [1h])))'
        )

    @property
    def adoption(self) -> str:
        return self._ratio(self.dau, f'({self.eligible} or on() vector(1))')

    @property
    def returning(self) -> str:
        return self._ratio(
            f'count(count by (user_id) (sum by (user_id) '
            f'(count_over_time({self.login} [1h])) > 1))',
            f'count(count by (user_id) (count_over_time({self.login} [1h])))',
        )

    @property
    def stickiness(self) -> str:
        return self._ratio(self.dau, self.mau)

    @property
    def total_24h(self) -> str:
        return f'sum(count_over_time({self.tele} [1h])) or vector(1)'

    def bool_rate(self, field: str) -> str:
        return self._ratio(
            f'sum(count_over_time({self.tele} | {field}="true" [1h]))',
            self.total_24h,
        )

    @staticmethod
    def _ratio(num: str, den: str, scale: float = 100) -> str:
        return f"({num} / ({den} or on() vector(1))) * {scale}"


def _auto_grid(index: int, cols: int = 2, w: int = 12, h: int = 7) -> dict:
    row, col = divmod(index, cols)
    return {"x": col * w, "y": row * h, "w": w, "h": h}


def build_users_obs_panel(
    metric: UsersObsMetric,
    ctx: UserObsContext,
    grid: dict,
    *,
    stat_panel: Callable[..., dict],
    gauge_panel: Callable[..., dict],
    timeseries_panel: Callable[..., dict],
    barchart_panel: Callable[..., dict],
    bargauge_panel: Callable[..., dict],
    piechart_panel: Callable[..., dict],
    DS_LOKI: dict,
    _loki_target: Callable[..., dict],
    _loki_instant_target: Callable[..., dict],
) -> dict:
    """Return one Grafana panel for a Users Observability Excel metric row."""
    title = metric["metric"]
    desc = users_metric_tooltip(metric)
    name = title
    c = ctx

    # ── Adoption ───────────────────────────────────────────────────────────
    if name == "Active Users (DAU/WAU/MAU)":
        return timeseries_panel(
            title,
            [
                _loki_target(c.dau, "DAU (24h)", "A"),
                _loki_target(c.wau, "WAU (7d)", "B"),
                _loki_target(c.mau, "MAU (30d)", "C"),
            ],
            unit="short", decimals=0, fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Adoption Rate":
        return gauge_panel(
            title, c.adoption, unit="percent", min_val=0, max_val=100,
            thresholds=[
                {"color": "red", "value": None},
                {"color": "yellow", "value": 15},
                {"color": "green", "value": 35},
            ],
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "New User Activation Rate":
        return barchart_panel(
            title,
            [
                _loki_instant_target(c.eligible, "Eligible"),
                _loki_instant_target(
                    f'sum(count_over_time({c.login} [1h])) or vector(0)', "Logins",
                ),
                _loki_instant_target(
                    f'sum(count_over_time({c.login} | is_new_user="true" [1h])) or vector(0)',
                    "New users",
                ),
            ],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Feature Adoption Rate":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sort_desc(topk(10, count by (feature_id) (count by (user_id, feature_id) '
                f'(count_over_time({c.tele} | user_id != "" | feature_id != "" [1h])))))',
                "{{feature_id}}",
            )],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "User Penetration by Department":
        return bargauge_panel(
            title,
            f'sort_desc('
            f'  count by (department) (count by (user_id, department) '
            f'    (count_over_time({c.tele_by_dept} | user_id != "" | department != "" [1h]))) '
            f'  / (max by (department) (max_over_time({c.tele_by_dept} | unwrap eligible_user_count [1h])) '
            f'     or on(department) vector(1)) * 100'
            f')',
            unit="percent", decimals=1, legend_format="{{department}}",
            color_mode="thresholds",
            thresholds=[
                {"color": "red", "value": None},
                {"color": "yellow", "value": 10},
                {"color": "green", "value": 25},
            ],
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Returning User Rate":
        return timeseries_panel(
            title, [_loki_target(c.returning, "Returning %", "A")],
            unit="percent", decimals=1, fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )

    # ── Engagement ─────────────────────────────────────────────────────────
    if name == "Sessions per User":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sort_desc(topk(20, sum by (user_id) (count_over_time({c.login} [1h]))))',
                "{{user_id}}",
            )],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Average Session Duration":
        return timeseries_panel(
            title,
            [_loki_target(
                f'avg(avg_over_time({c.tele} | unwrap session_time_ms [5m]))',
                "Avg session ms", "A",
            )],
            unit="ms", fill_opacity=25,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Prompts per Session":
        return timeseries_panel(
            title,
            [
                _loki_target(
                    f'avg(avg_over_time({c.tele} | unwrap turn_number [5m]))',
                    "Avg turns", "A",
                ),
                _loki_target(
                    f'quantile_over_time(0.95, {c.tele} | unwrap turn_number [5m])',
                    "p95 turns", "B",
                ),
            ],
            unit="short", fill_opacity=15,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Messages per Conversation":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sort_desc(topk(15, max by (session_id) (max_over_time({c.tele} | unwrap message_count [1h]))))',
                "{{session_id}}",
            )],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Stickiness (DAU/MAU)":
        return timeseries_panel(
            title,
            [
                _loki_target(c.stickiness, "DAU/MAU %", "A"),
            ],
            unit="percent", decimals=1, fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Feature Usage Distribution":
        return piechart_panel(
            title,
            [_loki_instant_target(
                f'topk(8, sum by (feature_id) (count_over_time({c.tele} | feature_id != "" [1h])))',
                "{{feature_id}}",
            )],
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Usage by Time of Day":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sort_desc(sum by (hour_of_day) (count_over_time({c.tele} | unwrap hour_of_day [1h])))',
                "Hour {{hour_of_day}}",
            )],
            unit="short", orientation="vertical",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Top 10 Users by Token":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'topk(10, sum by (user_id) (sum_over_time({c.tele} | unwrap total_tokens [1h])))',
                "{{user_id}}",
            )],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Top 10 Users by Session Duration":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'topk(10, sum by (user_id) (sum_over_time({c.tele} | unwrap session_time_ms [1h])))',
                "{{user_id}}",
            )],
            unit="ms", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )

    # ── Experience & DQ ────────────────────────────────────────────────────
    if name == "Task Completion Rate":
        return gauge_panel(
            title, c.bool_rate("task_completed"), unit="percent", min_val=0, max_val=100,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Response Acceptance Rate":
        return piechart_panel(
            title,
            [
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | response_accepted="true" [1h]))',
                    "Accepted",
                ),
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | response_accepted="false" [1h]))',
                    "Rejected",
                ),
            ],
            unit="short", grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Regeneration Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("regeneration"), "Regen %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Prompt Abandonment Rate":
        return barchart_panel(
            title,
            [
                _loki_instant_target(c.total_24h, "Started"),
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | prompt_abandoned="true" [1h]))',
                    "Abandoned",
                ),
            ],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Conversation Abandonment Rate":
        return barchart_panel(
            title,
            [
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | conversation_abandoned="false" [1h]))',
                    "Completed",
                ),
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | conversation_abandoned="true" [1h]))',
                    "Abandoned",
                ),
            ],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Error Encounter Rate":
        return timeseries_panel(
            title,
            [
                _loki_target(
                    f'sum(count_over_time({c.tele} | status="error" [5m]))',
                    "Errors", "A",
                ),
                _loki_target(
                    f'sum(count_over_time({c.tele} | status="success" [5m]))',
                    "Success", "B",
                ),
            ],
            unit="short", stacking="normal", fill_opacity=30,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Average Response Latency":
        return timeseries_panel(
            title,
            [
                _loki_target(
                    f'quantile_over_time(0.95, {c.tele} | unwrap latency_ms [5m])',
                    "p95", "A",
                ),
                _loki_target(
                    f'quantile_over_time(0.99, {c.tele} | unwrap latency_ms [5m])',
                    "p99", "B",
                ),
            ],
            unit="ms", fill_opacity=15,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Time to First Response":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'avg(avg_over_time({c.tele} | unwrap first_token_ms [1h]))',
                "Avg TTFT",
            )],
            unit="ms", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Escalation Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("escalation"), "Escalation %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Hallucination Feedback Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("hallucination_feedback"), "Feedback %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )

    # ── Productivity and Efficiency ────────────────────────────────────────
    if name == "Time Saved per Task":
        return timeseries_panel(
            title,
            [
                _loki_target(
                    f'avg(avg_over_time({c.tele} | unwrap time_saved_ms [5m]))',
                    "Avg", "A",
                ),
                _loki_target(
                    f'quantile_over_time(0.95, {c.tele} | unwrap time_saved_ms [5m])',
                    "p95", "B",
                ),
            ],
            unit="ms", fill_opacity=15,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Productivity Gain %":
        return bargauge_panel(
            title,
            f'avg(avg_over_time({c.tele} | unwrap productivity_gain_pct [1h]))',
            unit="percent", decimals=1, legend_format="Org avg",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Tasks Automated":
        return timeseries_panel(
            title,
            [_loki_target(
                f'sum(sum_over_time({c.tele} | task_automated="true" [1h]))',
                "Automated / hr", "A",
            )],
            unit="short", fill_opacity=25,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "AI-Assisted Task Rate":
        return barchart_panel(
            title,
            [
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | ai_assisted="true" [1h]))',
                    "AI-assisted",
                ),
                _loki_instant_target(
                    f'sum(count_over_time({c.tele} | ai_assisted="false" [1h]))',
                    "Manual",
                ),
            ],
            unit="short", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Resolution Time Reduction":
        return barchart_panel(
            title,
            [
                _loki_instant_target(
                    f'avg(avg_over_time({c.tele} | unwrap baseline_resolution_ms [1h]))',
                    "Baseline",
                ),
                _loki_instant_target(
                    f'avg(avg_over_time({c.tele} | unwrap resolution_time_ms [1h]))',
                    "With AI",
                ),
            ],
            unit="ms", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Employee Capacity Created":
        return timeseries_panel(
            title,
            [_loki_target(
                f'sum(sum_over_time({c.tele} | unwrap time_saved_ms [5m]))',
                "Time saved", "A",
            )],
            unit="ms", fill_opacity=30,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Revenue Influence":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sum by (department) (sum_over_time({c.tele_by_dept} | unwrap revenue_influence_usd [1h]))',
                "{{department}}",
            )],
            unit="currencyUSD", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Cost Avoidance":
        return barchart_panel(
            title,
            [_loki_instant_target(
                f'sum by (department) (sum_over_time({c.tele_by_dept} | unwrap cost_avoidance_usd [1h]))',
                "{{department}}",
            )],
            unit="currencyUSD", orientation="horizontal",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Conversion Lift":
        return stat_panel(
            title,
            f'avg(avg_over_time({c.tele} | unwrap conversion_lift_pct [1h]))',
            unit="percent", decimals=1,
            thresholds=[{"color": "green", "value": None}],
            color_mode="value",
            grid=grid, datasource=DS_LOKI, description=desc,
        )

    # ── Governance & Risk ──────────────────────────────────────────────────
    if name == "Sensitive Data Exposure Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("sensitive_data_exposure"), "Exposure %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "PII Submission Rate":
        return bargauge_panel(
            title,
            f'sort_desc(sum by (department) (count_over_time({c.tele_by_dept} | pii_submitted="true" [1h])))',
            unit="short", legend_format="{{department}}", color_mode="palette",
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Policy Violation Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("policy_violation"), "Violations %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Unsafe Output Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("unsafe_output"), "Unsafe %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Audit Coverage":
        return gauge_panel(
            title, c.bool_rate("audit_logged"), unit="percent", min_val=0, max_val=100,
            thresholds=[
                {"color": "red", "value": None},
                {"color": "yellow", "value": 80},
                {"color": "green", "value": 95},
            ],
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Access Violation Rate":
        return timeseries_panel(
            title,
            [
                _loki_target(
                    f'sum(count_over_time({c.tele} | access_violation="true" [5m]))',
                    "Violations", "A",
                ),
                _loki_target(
                    f'sum(count_over_time({c.tele} | access_violation="false" [5m]))',
                    "Allowed", "B",
                ),
            ],
            unit="short", stacking="normal", fill_opacity=25,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Human Review Rate":
        return timeseries_panel(
            title, [_loki_target(c.bool_rate("human_review"), "Review %", "A")],
            unit="percent", fill_opacity=20,
            grid=grid, datasource=DS_LOKI, description=desc,
        )
    if name == "Compliance Pass Rate":
        return gauge_panel(
            title, c.bool_rate("compliance_pass"), unit="percent", min_val=0, max_val=100,
            thresholds=[
                {"color": "red", "value": None},
                {"color": "yellow", "value": 85},
                {"color": "green", "value": 95},
            ],
            grid=grid, datasource=DS_LOKI, description=desc,
        )

    raise KeyError(f"No panel builder for metric: {name!r}")
