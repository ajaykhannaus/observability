"""Simulated kube-state-metrics / node-exporter / cAdvisor metrics.

Exposes Prometheus metrics that mimic what a real Kubernetes cluster would
produce via kube-state-metrics, node-exporter, and cAdvisor.  All metrics
are registered in the shared prometheus_client default registry so they appear
on the same /metrics endpoint that otel_metrics.py already serves on
PROMETHEUS_PORT (default 8000) — no second HTTP server needed.

Public API
----------
start_simulation()              Call once from runner.py after OTel setup.
update_load_signal(rps: float)  Call each batch to drive HPA scaling.

Standalone use
--------------
  python3 generator/pod_metrics_simulator.py
Opens its own HTTP server on port 8080 (or POD_METRICS_PORT env var).
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time

from prometheus_client import Counter, Gauge, Info, start_http_server

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cluster topology constants
# ---------------------------------------------------------------------------

NAMESPACE        = "ai-gateway-ns"
NODE_NAME        = "aks-nodepool1-12345678-0"
DEPLOYMENT_NAME  = "ai-gateway-deployment"
HPA_NAME         = "ai-gateway-hpa"
HPA_MIN          = 2
HPA_MAX          = 10
RPS_PER_REPLICA  = 1.5          # each pod handles ~1.5 req/s before HPA scales up
TICK_S           = 10           # background thread cadence (seconds)

# Realistic K8s pod name suffixes — a 5-char replicaset hash + 5-char pod suffix
_POD_SUFFIXES = [
    ("abc12", "xyz01"),
    ("abc12", "xyz02"),
    ("abc12", "xyz03"),
    ("def34", "pqr04"),
    ("def34", "pqr05"),
    ("ghi56", "lmn06"),
    ("ghi56", "lmn07"),
    ("ghi56", "lmn08"),
    ("ghi56", "lmn09"),
    ("ghi56", "lmn10"),
]

def _pod_name(rs: str, suffix: str) -> str:
    return f"ai-gateway-{rs}-{suffix}"

# ---------------------------------------------------------------------------
# Prometheus metric definitions
# ---------------------------------------------------------------------------

# ── kube-state-metrics ────────────────────────────────────────────────────
kube_pod_info = Gauge(
    "kube_pod_info",
    "Information about a pod.",
    ["namespace", "pod", "node", "created_by_kind", "created_by_name"],
)

kube_pod_status_phase = Gauge(
    "kube_pod_status_phase",
    "The pods current phase.",
    ["namespace", "pod", "phase"],
)

kube_pod_container_status_restarts_total = Counter(
    "kube_pod_container_status_restarts_total",
    "The number of container restarts per container.",
    ["namespace", "pod", "container"],
)

kube_deployment_spec_replicas = Gauge(
    "kube_deployment_spec_replicas",
    "Number of desired pods for a deployment.",
    ["namespace", "deployment"],
)

kube_deployment_status_replicas_available = Gauge(
    "kube_deployment_status_replicas_available",
    "Total number of available pods targeted by this deployment.",
    ["namespace", "deployment"],
)

kube_hpa_spec_min_replicas = Gauge(
    "kube_horizontalpodautoscaler_spec_min_replicas",
    "Lower limit for the number of pods that can be set by the autoscaler.",
    ["namespace", "hpa"],
)

kube_hpa_spec_max_replicas = Gauge(
    "kube_horizontalpodautoscaler_spec_max_replicas",
    "Upper limit for the number of pods that can be set by the autoscaler.",
    ["namespace", "hpa"],
)

kube_hpa_status_current_replicas = Gauge(
    "kube_horizontalpodautoscaler_status_current_replicas",
    "Current number of replicas of pods managed by this autoscaler.",
    ["namespace", "hpa"],
)

kube_hpa_status_desired_replicas = Gauge(
    "kube_horizontalpodautoscaler_status_desired_replicas",
    "Desired number of replicas of pods managed by this autoscaler.",
    ["namespace", "hpa"],
)

kube_node_status_condition = Gauge(
    "kube_node_status_condition",
    "The condition status of a cluster node.",
    ["node", "condition", "status"],
)

# ── node-exporter ─────────────────────────────────────────────────────────
node_cpu_seconds_total = Counter(
    "node_cpu_seconds_total",
    "Seconds the CPUs spent in each mode.",
    ["cpu", "mode"],
)

node_memory_MemAvailable_bytes = Gauge(
    "node_memory_MemAvailable_bytes",
    "Memory information field MemAvailable_bytes.",
)

node_memory_MemTotal_bytes = Gauge(
    "node_memory_MemTotal_bytes",
    "Memory information field MemTotal_bytes.",
)

# ── cAdvisor ─────────────────────────────────────────────────────────────
container_memory_rss = Gauge(
    "container_memory_rss",
    "Current RSS memory usage in bytes.",
    ["namespace", "pod", "container"],
)

container_cpu_usage_seconds_total = Counter(
    "container_cpu_usage_seconds_total",
    "Cumulative CPU time consumed in seconds.",
    ["namespace", "pod", "container"],
)

# ---------------------------------------------------------------------------
# Shared state (thread-safe via simple float volatile — good enough here)
# ---------------------------------------------------------------------------

_current_rps: float = 2.0          # updated by update_load_signal()
_current_replicas: int = 3          # actual pods running right now
_desired_replicas: int = 3          # what HPA wants
_active_pods: list[str] = []        # names of currently running pods

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Initialisation helpers
# ---------------------------------------------------------------------------

def _init_static_metrics() -> None:
    """Set metrics that don't change at runtime."""
    kube_hpa_spec_min_replicas.labels(namespace=NAMESPACE, hpa=HPA_NAME).set(HPA_MIN)
    kube_hpa_spec_max_replicas.labels(namespace=NAMESPACE, hpa=HPA_NAME).set(HPA_MAX)
    node_memory_MemTotal_bytes.set(8 * 1024 ** 3)   # 8 GB node

    # Node conditions — start healthy
    for condition in ("Ready", "DiskPressure", "MemoryPressure", "PIDPressure"):
        healthy = 1.0 if condition == "Ready" else 0.0
        kube_node_status_condition.labels(
            node=NODE_NAME, condition=condition, status="True"
        ).set(healthy)
        kube_node_status_condition.labels(
            node=NODE_NAME, condition=condition, status="False"
        ).set(1.0 - healthy)


def _rebuild_pods(n: int) -> list[str]:
    """Return list of n pod names and set their phase/info metrics."""
    pods = [_pod_name(rs, sfx) for rs, sfx in _POD_SUFFIXES[:n]]

    # Clear all phase labels first (set to 0 for pods that no longer exist)
    for rs, sfx in _POD_SUFFIXES:
        pname = _pod_name(rs, sfx)
        for phase in ("Running", "Pending", "Failed"):
            kube_pod_status_phase.labels(
                namespace=NAMESPACE, pod=pname, phase=phase
            ).set(0)

    for pod in pods:
        kube_pod_status_phase.labels(
            namespace=NAMESPACE, pod=pod, phase="Running"
        ).set(1)
        kube_pod_status_phase.labels(
            namespace=NAMESPACE, pod=pod, phase="Pending"
        ).set(0)
        kube_pod_status_phase.labels(
            namespace=NAMESPACE, pod=pod, phase="Failed"
        ).set(0)
        kube_pod_info.labels(
            namespace=NAMESPACE,
            pod=pod,
            node=NODE_NAME,
            created_by_kind="ReplicaSet",
            created_by_name=DEPLOYMENT_NAME,
        ).set(1)

    return pods


# ---------------------------------------------------------------------------
# Background simulation loop
# ---------------------------------------------------------------------------

def _simulate_loop() -> None:
    """Run forever in a daemon thread, updating all metrics every TICK_S seconds."""
    global _current_replicas, _desired_replicas, _active_pods

    _init_static_metrics()
    _current_replicas = 3
    _desired_replicas = 3
    _active_pods = _rebuild_pods(_current_replicas)

    kube_deployment_spec_replicas.labels(
        namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
    ).set(_current_replicas)
    kube_deployment_status_replicas_available.labels(
        namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
    ).set(_current_replicas)
    kube_hpa_status_current_replicas.labels(
        namespace=NAMESPACE, hpa=HPA_NAME
    ).set(_current_replicas)
    kube_hpa_status_desired_replicas.labels(
        namespace=NAMESPACE, hpa=HPA_NAME
    ).set(_desired_replicas)

    tick = 0
    while True:
        time.sleep(TICK_S)
        tick += 1

        with _lock:
            rps = _current_rps

        # ── HPA: compute desired replicas from load ───────────────────────
        raw_desired = max(HPA_MIN, min(HPA_MAX, round(rps / RPS_PER_REPLICA)))
        # Add small noise so the chart isn't a flat line
        raw_desired = max(HPA_MIN, min(HPA_MAX, raw_desired + random.randint(-1, 1)))

        with _lock:
            _desired_replicas = raw_desired
            # Ramp actual replicas toward desired by at most 1 per tick (K8s cooldown)
            if _current_replicas < _desired_replicas:
                _current_replicas = min(_current_replicas + 1, _desired_replicas)
            elif _current_replicas > _desired_replicas:
                _current_replicas = max(_current_replicas - 1, _desired_replicas)
            cur = _current_replicas
            des = _desired_replicas

        _active_pods = _rebuild_pods(cur)

        kube_deployment_spec_replicas.labels(
            namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
        ).set(cur)
        kube_deployment_status_replicas_available.labels(
            namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
        ).set(cur)
        kube_hpa_status_current_replicas.labels(
            namespace=NAMESPACE, hpa=HPA_NAME
        ).set(cur)
        kube_hpa_status_desired_replicas.labels(
            namespace=NAMESPACE, hpa=HPA_NAME
        ).set(des)

        # ── Container metrics per active pod ────────────────────────────────
        for pod in _active_pods:
            mem_bytes = random.uniform(150_000_000, 280_000_000)
            container_memory_rss.labels(
                namespace=NAMESPACE, pod=pod, container="ai-gateway"
            ).set(mem_bytes)
            cpu_inc = random.uniform(0.01, 0.06)
            container_cpu_usage_seconds_total.labels(
                namespace=NAMESPACE, pod=pod, container="ai-gateway"
            ).inc(cpu_inc)

            # 0.5% chance of a restart per pod per tick
            if random.random() < 0.005:
                kube_pod_container_status_restarts_total.labels(
                    namespace=NAMESPACE, pod=pod, container="ai-gateway"
                ).inc()
                # Briefly mark as Pending, then back to Running
                kube_pod_status_phase.labels(
                    namespace=NAMESPACE, pod=pod, phase="Running"
                ).set(0)
                kube_pod_status_phase.labels(
                    namespace=NAMESPACE, pod=pod, phase="Pending"
                ).set(1)
                kube_deployment_status_replicas_available.labels(
                    namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
                ).set(max(1, cur - 1))
                time.sleep(2)
                kube_pod_status_phase.labels(
                    namespace=NAMESPACE, pod=pod, phase="Running"
                ).set(1)
                kube_pod_status_phase.labels(
                    namespace=NAMESPACE, pod=pod, phase="Pending"
                ).set(0)
                kube_deployment_status_replicas_available.labels(
                    namespace=NAMESPACE, deployment=DEPLOYMENT_NAME
                ).set(cur)
                logger.info("Pod %s restarted (simulated)", pod)

        # ── Node metrics ─────────────────────────────────────────────────
        mem_available = random.uniform(1_800_000_000, 4_200_000_000)
        node_memory_MemAvailable_bytes.set(mem_available)

        node_cpu_seconds_total.labels(cpu="0", mode="user").inc(
            random.uniform(0.05, 0.15)
        )
        node_cpu_seconds_total.labels(cpu="0", mode="idle").inc(
            random.uniform(0.5, 0.8)
        )

        # Node pressure — MemoryPressure True when < 1.5 GB available
        mem_pressure = 1.0 if mem_available < 1_500_000_000 else 0.0
        kube_node_status_condition.labels(
            node=NODE_NAME, condition="MemoryPressure", status="True"
        ).set(mem_pressure)
        kube_node_status_condition.labels(
            node=NODE_NAME, condition="MemoryPressure", status="False"
        ).set(1.0 - mem_pressure)

        if tick % 6 == 0:   # log every ~60s
            logger.info(
                "Pod sim | rps=%.2f pods=%d/%d (cur/desired)",
                rps, cur, des,
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_started = False
_started_lock = threading.Lock()


def start_simulation() -> None:
    """Start the background simulation thread.

    Does NOT start an HTTP server — assumes the caller (runner.py via
    otel_metrics.py) already started prometheus_client's HTTP server on
    PROMETHEUS_PORT.  All metrics registered above share the same default
    registry and appear automatically on that endpoint.
    """
    global _started
    with _started_lock:
        if _started:
            return
        _started = True

    t = threading.Thread(target=_simulate_loop, daemon=True, name="pod-sim")
    t.start()
    logger.info("Pod metrics simulator started (sharing existing /metrics endpoint)")


def update_load_signal(rps: float) -> None:
    """Inform the simulator of current request-per-second load.

    Called by runner.py after each batch:
        update_load_signal(batch_size / BATCH_INTERVAL_S)

    The background thread uses this to compute the HPA desired replica count.
    """
    global _current_rps
    with _lock:
        # Smooth with EMA to avoid spiky HPA behaviour
        _current_rps = 0.8 * _current_rps + 0.2 * rps


# ---------------------------------------------------------------------------
# Standalone entry point (for isolated testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    port = int(os.getenv("POD_METRICS_PORT", "8080"))
    start_http_server(port)
    logger.info("Pod metrics simulator standalone: http://localhost:%d/metrics", port)
    start_simulation()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped.")
