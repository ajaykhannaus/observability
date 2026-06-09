"""Panel title → info-tooltip text for Grafana metric panels (ⓘ icon)."""

METRIC_DEFINITIONS: dict[str, str] = {
    # 1 — Infrastructure observability
    "CPU utilization": (
        "Average CPU usage per running gateway pod: "
        "`rate(container_cpu_usage_seconds_total) / running_pod_count × 100`."
    ),
    "Model throughput": (
        "Completion (output) tokens per second: "
        "`rate(ai_gateway_request_token_total{token_type=\"completion\"})`."
    ),
    "OOM failures": (
        "Container out-of-memory kills in the gateway namespace (`kube_pod_container_oom_killed_total`)."
    ),
    "Pod/container health": (
        "Deployment readiness: available replicas ÷ desired replicas × 100."
    ),
    "Auto-scaling events": (
        "HPA scale-up/down events in 24h (`kube_horizontalpodautoscaler_scaling_events_total`)."
    ),
    "API error rate": (
        "Gateway API errors: `request_count{status=error}` ÷ total requests × 100."
    ),
    "HPA Current vs Desired Replicas": (
        "Horizontal Pod Autoscaler current, desired, min, and max replica counts."
    ),
    "Pod Restart Rate": (
        "Container restart rate per pod — elevated values suggest crash loops."
    ),
    "Container Memory RSS by Pod": (
        "Resident memory (`container_memory_rss`) per ai-gateway pod."
    ),
    "Container CPU Usage by Pod": (
        "CPU cores used per pod (`rate(container_cpu_usage_seconds_total)`)."
    ),
    "Node Memory Available Over Time": (
        "Cluster node memory available vs total — capacity headroom."
    ),
    "Node CPU Usage (user mode)": (
        "Node CPU time in user mode — host-level utilization."
    ),
    "Batch Duration Heatmap": (
        "Distribution of synthetic telemetry runner batch processing times (`ai_telemetry_runner_batch_duration_seconds`)."
    ),
    "Kafka Queue Depth": (
        "Current depth of the runner publish queue (`ai_telemetry_runner_kafka_queue_depth` gauge)."
    ),
    "Publish Error Rate by Reason": (
        "Events published per second (`ai_gateway_request_count_total`) plus publish "
        "failures by `reason` when present (`ai_telemetry_runner_publish_errors_total`)."
    ),
    "Runner Event Publish Rate": (
        "Events published per second (`ai_gateway_request_count_total`) plus publish "
        "failures by `reason` when present (`ai_telemetry_runner_publish_errors_total`)."
    ),
    "Batch Duration p99": (
        "99th percentile batch processing duration — tail latency of the telemetry pipeline."
    ),
    "Collector Exporter Queue Size": (
        "OTel Collector exporter queue depth (Tempo, Loki) plus export throughput "
        "(spans/s, logs/s) — backpressure and pipeline activity."
    ),
    "Collector Export Failures": (
        "Rate of failed span and log exports from the OTel Collector."
    ),
    "Collector Export Throughput & Failures": (
        "Collector export throughput (spans/s, logs/s) and failure rates — "
        "shows pipeline activity even when failures are zero."
    ),

    # 2 — Network observability
    "End-to-end response latency": (
        "Average gateway response time in ms: histogram `_sum / _count` on `ai_gateway_request_duration_milliseconds`. "
        "Includes queue, model, and streaming time."
    ),
    "Request Latency — p50 / p95 / p99": (
        "Latency percentiles from the request duration histogram. "
        "p99 is the slowest 1% — the usual SLO pain metric."
    ),
    "Queue delays": (
        "Average `queue_wait_ms` before the model starts processing. "
        "Source: Loki — separates queue congestion from model inference time."
    ),

    # 3 — AI observability
    "Model Specific Latency": (
        "p95 end-to-end latency per `model_name`. "
        "Compares which models are consistently slower."
    ),
    "First token latency (Model Based)": (
        "Average `first_token_ms` for streaming requests, by model. "
        "Source: Loki logs — time until the first token is returned to the client."
    ),
    "Hallucination rate": (
        "% of judged responses where `faithfulness < 5` (0–10 judge scale). "
        "Source: Loki `eval_result` events (~sampled traffic). Requires evaluator enabled."
    ),
    "Hallucination rate over time": (
        "Rolling 5m percentage of judged responses flagged as hallucinating (`faithfulness < 5`). "
        "Source: Loki `eval_result` events."
    ),
    "Factual accuracy": (
        "Mean judge `faithfulness` × 10, shown as 0–100%. "
        "Higher is more factually aligned with sources/context."
    ),
    "Relevance score": (
        "Mean judge `relevance` (0–10): does the answer address the user question?"
    ),
    "Groundedness score": (
        "Mean judge `groundedness` (0–10): are claims supported by provided context/sources?"
    ),
    "Evaluation Coverage": (
        "% of telemetry events that also have an `eval_result` log — how much traffic is quality-judged."
    ),
    "Evaluator Errors (24h)": (
        "Count of eval runs that failed (e.g. judge timeout) in 24h. "
        "Non-zero means quality pipeline is unhealthy."
    ),
    "Low-Quality Responses (hallucination flagged)": (
        "Raw `eval_result` logs where faithfulness failed — drill-down for bad responses."
    ),
    "Live token generation rate": (
        "1-minute stacked completion-token rate by model — near-real-time output."
    ),
    "Streaming response latency": (
        "Average `stream_response_ms` by model for streaming responses."
    ),
    "Streaming tokens/sec": (
        "Observed streaming `tokens_per_second` ranked by model."
    ),
    "Avg stream tokens/s": (
        "Platform-wide average streaming throughput from logs."
    ),

    # 4 — Data observability
    "Total requests": (
        "Headline request count for the dashboard time range with an inline sparkline trend. "
        "Source: Prometheus `increase(ai_gateway_request_count_total[$__range])`."
    ),
    "RPM": (
        "Continuous requests-per-minute rate over time. "
        "Source: `rate(ai_gateway_request_count_total[$__rate_interval]) × 60`."
    ),
    "RPM by Model": (
        "Stacked area chart of requests/min for the top 6 `model_name` series — "
        "stack height is total RPM from those models."
    ),
    "RPM by Model (current)": (
        "Ranked bar gauge of current requests/min for the top 6 models. "
        "Companion snapshot to the stacked time series."
    ),
    "RPM Share by Model": (
        "Donut chart of each model's percentage of total RPM (top 6). "
        "Shows traffic share rather than absolute request volume."
    ),
    "Error RPM by Model": (
        "Failed requests per minute for the top 6 models. "
        "Source: `rate(ai_gateway_exception_count_total[$__rate_interval]) × 60`."
    ),
    "Error Rate by Model": (
        "Failed requests as a percentage per model (`exception_count / request_count × 100`). "
        "Yellow/red threshold bands highlight SLA breach territory (5% / 10%)."
    ),
    "Model Provider Distribution": (
        "Share of requests by vendor (`model_provider`: anthropic, openai, google, etc.) over the last hour."
    ),
    "Model Distribution (last 1h)": (
        "Ranked horizontal bar gauge of request volume by `model_name` over the last hour — "
        "model names on the left, values on the bars."
    ),
    "Requests / min by Model": (
        "Live request rate per model (`rate × 60`). "
        "Filtered by department template variable when set."
    ),
    "Requests / min by Department": (
        "Live request rate per `department` — which org teams drive load."
    ),
    "Requests / min by Operation": (
        "Traffic by `operation_name` (e.g. chat_completion, code_generation) — use-case mix."
    ),
    "Error Rate by Type": (
        "Failed requests per second by `error_type` (rate_limit, timeout, model_unavailable, etc.)."
    ),
    "Errors by HTTP Status Code (last 1h)": (
        "Volume of exceptions in the last hour grouped by `http_status` (429, 504, 500, …)."
    ),
    "Error Category Mix": (
        "Failures grouped by `error_category` (throttling, availability, auth, validation)."
    ),
    "SLA Breach Rate by Department": (
        "Per-department error rate (%): requests with `status=error` ÷ all requests. "
        "High values mean that department is missing SLA targets."
    ),
    "Total Requests by Routing Reason (last 1h)": (
        "Why the gateway chose a model (`routing_reason` from logs): cost, latency, fallback, policy, etc. "
        "Source: Loki `telemetry_event`."
    ),
    "Live Telemetry Events": (
        "Streaming audit of recent `telemetry_event` log lines — request id, model, latency, department, routing. "
        "Use for live debugging; not aggregated metrics."
    ),

    # 5 — User observability
    "Active users": (
        "Distinct `user_id` values in telemetry logs over the last 5 minutes (headline stat, last value). "
        "Source: Loki `telemetry_event`."
    ),
    "Active sessions": (
        "Distinct `session_id` values in telemetry logs over the last 5 minutes (headline stat, last value). "
        "Source: Loki `telemetry_event`."
    ),
    "Logins (24h)": (
        "Count of `login_event` logs in 24h — emitted when a new session starts (turn 1)."
    ),
    "Active users (24h)": (
        "Distinct `user_id` values with at least one `telemetry_event` in 24h."
    ),
    "Active users by department (24h)": (
        "Distinct active users per `department` in the last 24h — ranked horizontal bars. "
        "The department filter is intentionally ignored so all departments are visible."
    ),
    "Monthly active users (30d)": (
        "Distinct users with a `login_event` in the last 30 days — monthly active user (MAU) proxy."
    ),
    "LLM usage spike (15m vs prev 15m)": (
        "Percent change in total tokens: last 15m vs the prior 15m window "
        "(computed as [15m] ÷ ([30m] − [15m])). High values indicate sudden platform-wide LLM usage growth."
    ),
    "Login track": (
        "Login/session-start events per 5m bucket from `login_event` logs."
    ),
    "Users added (daily active logins)": (
        "Distinct users with a `login_event` in the last 24h."
    ),
    "Daily active users (24h)": (
        "Distinct users with a `login_event` in the last 24h (stat with sparkline)."
    ),
    "Top 10 users — tokens (24h)": (
        "Users ranked by sum of `total_tokens` over 24h — who is consuming the most tokens."
    ),
    "Top 10 users — token rate (5m)": (
        "Instant bar chart — top 10 users by tokens consumed in the last 5m."
    ),
    "Top 10 users — session time (6h)": (
        "Users ranked by summed `session_time_ms` — wall-clock time spent in sessions "
        "(reading, typing, waiting), not per-request API latency."
    ),
    "Session usage by user": (
        "Per `user_id` + `department` token totals (top 50, 6h) — avoids high-cardinality session series."
    ),
    "Top users by tokens (6h)": (
        "Table of top 50 users by total tokens in 6h, with department."
    ),
    "Session time by user (top 10, 5m)": (
        "Instant bar chart — top 10 users by summed `session_time_ms` in the last 5m."
    ),
    "Token volume — spike detector (5m buckets)": (
        "Total token volume per 5m vs 1h average per 5m bucket — visual spike detection for LLM usage."
    ),
    "Top 10 users — spike ratio (15m vs prev 15m)": (
        "Per-user ratio: tokens in last 15m ÷ tokens in the previous 15m (30m window minus last 15m). "
        "Values above ~1.5 mean usage in the last 15m exceeds the prior 15m baseline."
    ),
    "Recent login events": (
        "Live stream of `login_event` logs with user, session, department, and auth method."
    ),

    # 6 — Cost & Usage Observability
    "Cost per request": (
        "Average USD cost per request from log field `cost_usd`. "
        "Source: Loki `telemetry_event` (synthetic generator computes per-model pricing)."
    ),
    "Cost per user/session": (
        "Average cost per request over time — `increase(cost) / increase(requests)` from Prometheus "
        "(Loki per-user ratios exceed local query resolution limits in range mode)."
    ),
    "Daily/monthly spend": (
        "Total USD spent: 24h and 30d `increase` on `ai_gateway_request_cost_USD_total`."
    ),
    "Cost by department": (
        "Share of 24h LLM spend by internal department (`department` label). "
        "Each user belongs to one department within the organization."
    ),
    "Total cost breakdown": (
        "Share of 24h spend by `model_name` — ranked cost drivers."
    ),
    "Model-wise cost breakdown": (
        "24h spend ranked by `model_name` — which models are most expensive."
    ),
    "Cache hit savings": (
        "USD saved via prompt cache hits (`cache_savings_usd` in logs). "
        "Higher values mean caching is reducing billable prompt tokens."
    ),
    "Output tokens": (
        "Total completion (output) tokens consumed in the selected time range."
    ),
    "Avg tokens / request": (
        "Average prompt + completion + cache tokens per request."
    ),
    "Context fill": (
        "Average context window utilization % — how full prompts are across models."
    ),
    "Errors (5m)": (
        "Exception count increase in the last 5 minutes."
    ),
    "Output Token Count": (
        "Stacked completion-token throughput by model — each band is one model's share."
    ),
    "Output tokens by model (now)": (
        "Instantaneous completion tokens/s per model — ranked horizontal bar gauge."
    ),
    "Token type mix": (
        "Donut chart of prompt vs completion vs cache_read tokens in the last hour."
    ),
    "Total tokens per request": (
        "Average total tokens per request over time (single aggregate trend line)."
    ),
    "Prompt size by model": (
        "Average prompt tokens per request ranked by model — horizontal bar gauge."
    ),
    "Context Window Utilization (%)": (
        "Dial gauge of average context window fill — high values risk truncation."
    ),
    "Context fill by model": (
        "Context window utilization % ranked by model."
    ),
    "Prompt size trend": (
        "Average prompt (input) tokens per request over time — drives cost and latency."
    ),
    "Errors by type (5m)": (
        "Bar chart of exceptions in the last 5m broken down by `error_type`."
    ),
    "Prompt size": (
        "Average prompt (input) tokens per request — drives cost and latency."
    ),
    "Tokens/sec": (
        "Observed `tokens_per_second` from streaming requests in logs."
    ),
    "Real-time error spikes": (
        "Exception count increase over 5m and per-minute error rate — detects sudden failure bursts."
    ),

    # 7 — Safety and security
    "Toxicity score": (
        "Average content-safety `toxicity_score` on prompt logs, scaled to 0–100%. "
        "Source: Loki `prompt_log_event`."
    ),
    "PII detection rate": (
        "% of prompt logs with `pii_detected=true`. "
        "PII must stay in logs, not Prometheus labels."
    ),
    "Prompt injection attempts": (
        "Count of prompts flagged `prompt_injection_detected=true` in 24h."
    ),
    "Jailbreak attempts": (
        "Count of prompts flagged `jailbreak_attempt=true` (role-override / DAN-style) in 24h."
    ),
    "Compliance violations": (
        "Count of prompts with `compliance_violation=true` (policy / classification breach) in 24h."
    ),
    "Prompt injection attempts / min": (
        "Rate of detected injection attempts per minute — live security trend."
    ),
    "Jailbreak attempts / min": (
        "Rate of jailbreak detections per minute."
    ),
    "Compliance violations / min": (
        "Rate of compliance violations per minute."
    ),
    "PII Events Today": (
        "Total prompt logs with PII detected in the last 24h."
    ),
    "PHI Requests Today": (
        "Telemetry events with `data_classification=phi` (protected health information) in 24h."
    ),
    "PII Requests Today": (
        "Telemetry events with `data_classification=pii` in 24h."
    ),
    "Unique Prompt Hashes (24h)": (
        "Distinct `prompt_hash` values audited — coverage of prompt logging without storing raw text."
    ),
    "Data Classification Distribution": (
        "Mix of `data_classification` labels (phi, pii, confidential, internal, …) in the last hour."
    ),
    "PII Events by Department": (
        "PII detections per `department` over time — which teams send sensitive prompts."
    ),
    "PHI + PII Volume by Department (last 1h)": (
        "Requests handling phi or pii classification, by department, last hour."
    ),
    "Prompt Log Events (PII-scrubbed)": (
        "Audit stream of `prompt_log_event` entries — content is scrubbed; use for security review."
    ),
    "PHI / PII Requests with Trace Links": (
        "Table of sensitive requests with `request_id`, department, model, and `trace_id` for Tempo drill-down."
    ),
    "Safety incidents (injection, jailbreak, compliance)": (
        "Log lines where any safety flag fired — combined injection, jailbreak, or compliance events."
    ),
}
