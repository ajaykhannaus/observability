"""Users Observability Metrics — mirrors Users Observability Metrics.xlsx."""

from __future__ import annotations

from typing import TypedDict


class UsersObsMetric(TypedDict):
    category: str
    metric: str
    definition: str
    key_data_elements: str
    best_visual: str


USERS_OBSERVABILITY_METRICS: list[UsersObsMetric] = [
    # ── Adoption ───────────────────────────────────────────────────────────
    {"category": "Adoption", "metric": "Active Users (DAU/WAU/MAU)",
     "definition": "Unique users interacting with AI",
     "key_data_elements": "User ID, Timestamp", "best_visual": "Time-series Line Chart"},
    {"category": "Adoption", "metric": "Adoption Rate",
     "definition": "% eligible users using AI",
     "key_data_elements": "User ID, Eligible Population", "best_visual": "Gauge / KPI Card"},
    {"category": "Adoption", "metric": "New User Activation Rate",
     "definition": "First successful AI usage",
     "key_data_elements": "User ID, Activation Event", "best_visual": "Funnel Chart"},
    {"category": "Adoption", "metric": "Feature Adoption Rate",
     "definition": "Usage of specific capability",
     "key_data_elements": "Feature ID, User ID", "best_visual": "Horizontal Bar Chart"},
    {"category": "Adoption", "metric": "User Penetration by Department",
     "definition": "Adoption across org units",
     "key_data_elements": "Department, User ID", "best_visual": "Heatmap"},
    {"category": "Adoption", "metric": "Returning User Rate",
     "definition": "Users coming back",
     "key_data_elements": "User ID, Session Date", "best_visual": "Cohort Retention Chart"},
    # ── Engagement ─────────────────────────────────────────────────────────
    {"category": "Engagement", "metric": "Sessions per User",
     "definition": "Usage frequency",
     "key_data_elements": "Session ID, User ID", "best_visual": "Histogram"},
    {"category": "Engagement", "metric": "Average Session Duration",
     "definition": "Time spent using AI",
     "key_data_elements": "Session Start/End", "best_visual": "Trend Line"},
    {"category": "Engagement", "metric": "Prompts per Session",
     "definition": "Interaction intensity",
     "key_data_elements": "Prompt ID, Session ID", "best_visual": "Box Plot"},
    {"category": "Engagement", "metric": "Messages per Conversation",
     "definition": "Conversation depth",
     "key_data_elements": "Conversation ID", "best_visual": "Distribution Histogram"},
    {"category": "Engagement", "metric": "Stickiness (DAU/MAU)",
     "definition": "Repeat engagement",
     "key_data_elements": "User ID, Date", "best_visual": "Gauge + Trend Line"},
    {"category": "Engagement", "metric": "Feature Usage Distribution",
     "definition": "Feature popularity",
     "key_data_elements": "Feature ID", "best_visual": "Treemap"},
    {"category": "Engagement", "metric": "Usage by Time of Day",
     "definition": "Peak usage periods",
     "key_data_elements": "Timestamp", "best_visual": "24-Hour Heatmap"},
    {"category": "Engagement", "metric": "Top 10 Users by Token",
     "definition": "Highest token consumers",
     "key_data_elements": "User ID, Token Count", "best_visual": "Ranked Bar Chart"},
    {"category": "Engagement", "metric": "Top 10 Users by Session Duration",
     "definition": "Most engaged users",
     "key_data_elements": "User ID, Session Duration", "best_visual": "Ranked Bar Chart"},
    # ── Experience & DQ ────────────────────────────────────────────────────
    {"category": "Experience & DQ", "metric": "Task Completion Rate",
     "definition": "Tasks successfully completed",
     "key_data_elements": "Task Status", "best_visual": "Gauge"},
    {"category": "Experience & DQ", "metric": "Response Acceptance Rate",
     "definition": "Accepted responses",
     "key_data_elements": "Response Status", "best_visual": "Donut Chart"},
    {"category": "Experience & DQ", "metric": "Regeneration Rate",
     "definition": "Retry frequency",
     "key_data_elements": "Regeneration Event", "best_visual": "Trend Line"},
    {"category": "Experience & DQ", "metric": "Prompt Abandonment Rate",
     "definition": "Prompts not completed",
     "key_data_elements": "Prompt Status", "best_visual": "Funnel Chart"},
    {"category": "Experience & DQ", "metric": "Conversation Abandonment Rate",
     "definition": "Sessions ending prematurely",
     "key_data_elements": "Session Outcome", "best_visual": "Sankey Diagram"},
    {"category": "Experience & DQ", "metric": "Error Encounter Rate",
     "definition": "User-facing failures",
     "key_data_elements": "Error Events", "best_visual": "Stacked Area Chart"},
    {"category": "Experience & DQ", "metric": "Average Response Latency",
     "definition": "User wait time",
     "key_data_elements": "Request Time, Response Time", "best_visual": "P95/P99 Trend Line"},
    {"category": "Experience & DQ", "metric": "Time to First Response",
     "definition": "Initial response speed",
     "key_data_elements": "Prompt Timestamp", "best_visual": "Latency Distribution Histogram"},
    {"category": "Experience & DQ", "metric": "Escalation Rate",
     "definition": "Human handoff frequency",
     "key_data_elements": "Escalation Event", "best_visual": "Trend Line"},
    {"category": "Experience & DQ", "metric": "Hallucination Feedback Rate",
     "definition": "Incorrect answer reports",
     "key_data_elements": "Feedback Type", "best_visual": "Control Chart"},
    # ── Productivity and Efficiency ──────────────────────────────────────
    {"category": "Productivity and Efficiency", "metric": "Time Saved per Task",
     "definition": "Efficiency gain",
     "key_data_elements": "Task Duration", "best_visual": "Box Plot"},
    {"category": "Productivity and Efficiency", "metric": "Productivity Gain %",
     "definition": "Performance improvement",
     "key_data_elements": "Baseline vs Current", "best_visual": "Bullet Chart"},
    {"category": "Productivity and Efficiency", "metric": "Tasks Automated",
     "definition": "AI-completed tasks",
     "key_data_elements": "Automation Events", "best_visual": "Cumulative Trend Line"},
    {"category": "Productivity and Efficiency", "metric": "AI-Assisted Task Rate",
     "definition": "AI usage in workflows",
     "key_data_elements": "AI Usage Flag", "best_visual": "Stacked Bar Chart"},
    {"category": "Productivity and Efficiency", "metric": "Resolution Time Reduction",
     "definition": "Faster completion",
     "key_data_elements": "Resolution Duration", "best_visual": "Before/After Bar Chart"},
    {"category": "Productivity and Efficiency", "metric": "Employee Capacity Created",
     "definition": "Time freed up",
     "key_data_elements": "Time Saved", "best_visual": "Area Chart"},
    {"category": "Productivity and Efficiency", "metric": "Revenue Influence",
     "definition": "Revenue attributed to AI",
     "key_data_elements": "Revenue Events", "best_visual": "Waterfall Chart"},
    {"category": "Productivity and Efficiency", "metric": "Cost Avoidance",
     "definition": "Savings generated",
     "key_data_elements": "Cost Baseline", "best_visual": "Waterfall Chart"},
    {"category": "Productivity and Efficiency", "metric": "Conversion Lift",
     "definition": "Conversion improvement",
     "key_data_elements": "Conversion Events", "best_visual": "Experiment Lift Chart"},
    # ── Governance & Risk ──────────────────────────────────────────────────
    {"category": "Governance & Risk", "metric": "Sensitive Data Exposure Rate",
     "definition": "Restricted content entered",
     "key_data_elements": "Classification Results", "best_visual": "Risk Trend Line"},
    {"category": "Governance & Risk", "metric": "PII Submission Rate",
     "definition": "Personal data submissions",
     "key_data_elements": "PII Detection", "best_visual": "Heatmap"},
    {"category": "Governance & Risk", "metric": "Policy Violation Rate",
     "definition": "Content violations",
     "key_data_elements": "Moderation Results", "best_visual": "Control Chart"},
    {"category": "Governance & Risk", "metric": "Unsafe Output Rate",
     "definition": "Unsafe AI responses",
     "key_data_elements": "Safety Flags", "best_visual": "Trend Line"},
    {"category": "Governance & Risk", "metric": "Audit Coverage",
     "definition": "Logged interactions",
     "key_data_elements": "Audit Logs", "best_visual": "Gauge"},
    {"category": "Governance & Risk", "metric": "Access Violation Rate",
     "definition": "Unauthorized attempts",
     "key_data_elements": "Access Logs", "best_visual": "Stacked Bar Chart"},
    {"category": "Governance & Risk", "metric": "Human Review Rate",
     "definition": "Human-reviewed interactions",
     "key_data_elements": "Review Events", "best_visual": "Trend Line"},
    {"category": "Governance & Risk", "metric": "Compliance Pass Rate",
     "definition": "Compliance success",
     "key_data_elements": "Compliance Results", "best_visual": "Gauge"},
]

USERS_OBS_SECTIONS: tuple[str, ...] = (
    "Adoption",
    "Engagement",
    "Experience & DQ",
    "Productivity and Efficiency",
    "Governance & Risk",
)


def metrics_by_section() -> dict[str, list[UsersObsMetric]]:
    out: dict[str, list[UsersObsMetric]] = {s: [] for s in USERS_OBS_SECTIONS}
    for m in USERS_OBSERVABILITY_METRICS:
        out[m["category"]].append(m)
    return out


def users_metric_tooltip(m: UsersObsMetric) -> str:
    return (
        f"**Category:** {m['category']}\n\n"
        f"**Metric:** {m['metric']}\n\n"
        f"**Definition:** {m['definition']}\n\n"
        f"**Key Data Elements:** {m['key_data_elements']}\n\n"
        f"**Best Visual:** {m['best_visual']}"
    )


def register_metric_definitions(target: dict[str, str]) -> None:
    """Populate METRIC_DEFINITIONS tooltips from the Excel spec."""
    for m in USERS_OBSERVABILITY_METRICS:
        target[m["metric"]] = users_metric_tooltip(m)
