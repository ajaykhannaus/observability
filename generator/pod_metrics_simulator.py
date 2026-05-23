import time
import random
import threading
from prometheus_client import start_http_server, Gauge, Counter

# ---------------------------------------------------------------------------
# kube-state-metrics simulation
# ---------------------------------------------------------------------------
kube_pod_status_phase = Gauge('kube_pod_status_phase', 'The pods current phase.', ['namespace', 'pod', 'phase'])
kube_pod_container_status_restarts_total = Counter('kube_pod_container_status_restarts_total', 'The number of container restarts per container.', ['namespace', 'pod', 'container'])
kube_hpa_spec_min_replicas = Gauge('kube_horizontalpodautoscaler_spec_min_replicas', 'Lower limit for the number of pods that can be set by the autoscaler.', ['namespace', 'hpa'])
kube_hpa_spec_max_replicas = Gauge('kube_horizontalpodautoscaler_spec_max_replicas', 'Upper limit for the number of pods that can be set by the autoscaler.', ['namespace', 'hpa'])
kube_deployment_spec_replicas = Gauge('kube_deployment_spec_replicas', 'Number of desired pods for a deployment.', ['namespace', 'deployment'])
kube_deployment_status_replicas_available = Gauge('kube_deployment_status_replicas_available', 'Total number of available pods (ready for at least minReadySeconds) targeted by this deployment.', ['namespace', 'deployment'])

# ---------------------------------------------------------------------------
# node-exporter / system-level simulation
# ---------------------------------------------------------------------------
node_cpu_seconds_total = Counter('node_cpu_seconds_total', 'Seconds the cpus spent in each mode.', ['cpu', 'mode'])
node_memory_MemAvailable_bytes = Gauge('node_memory_MemAvailable_bytes', 'Memory information field MemAvailable_bytes.')

# ---------------------------------------------------------------------------
# cAdvisor / container metrics simulation
# ---------------------------------------------------------------------------
container_memory_rss = Gauge('container_memory_rss', 'Current RSS memory usage in bytes.', ['namespace', 'pod', 'container'])
container_cpu_usage_seconds_total = Counter('container_cpu_usage_seconds_total', 'Cumulative cpu time consumed in seconds.', ['namespace', 'pod', 'container'])

def simulate_metrics():
    """Background thread to continuously update simulated metrics."""
    namespace = "ai-gateway-ns"
    pod_name = "ai-gateway-pod-1"
    container_name = "ai-gateway"
    deployment_name = "ai-gateway-deployment"
    hpa_name = "ai-gateway-hpa"

    # Static init
    kube_hpa_spec_min_replicas.labels(namespace=namespace, hpa=hpa_name).set(2)
    kube_hpa_spec_max_replicas.labels(namespace=namespace, hpa=hpa_name).set(10)
    kube_deployment_spec_replicas.labels(namespace=namespace, deployment=deployment_name).set(3)
    kube_deployment_status_replicas_available.labels(namespace=namespace, deployment=deployment_name).set(3)
    
    # Pod phase breakdown (1=true, 0=false in true KSM style)
    kube_pod_status_phase.labels(namespace=namespace, pod=pod_name, phase='Running').set(1)
    kube_pod_status_phase.labels(namespace=namespace, pod=pod_name, phase='Pending').set(0)
    kube_pod_status_phase.labels(namespace=namespace, pod=pod_name, phase='Failed').set(0)

    while True:
        # Simulate memory usage fluctuating between 150MB and 250MB
        mem_rss = random.uniform(150_000_000, 250_000_000)
        container_memory_rss.labels(namespace=namespace, pod=pod_name, container=container_name).set(mem_rss)
        
        # Increment CPU usage slightly
        cpu_inc = random.uniform(0.01, 0.05)
        container_cpu_usage_seconds_total.labels(namespace=namespace, pod=pod_name, container=container_name).inc(cpu_inc)
        
        node_cpu_seconds_total.labels(cpu='0', mode='user').inc(random.uniform(0.05, 0.1))
        node_memory_MemAvailable_bytes.set(random.uniform(2_000_000_000, 4_000_000_000))
        
        # Occasionally simulate a restart (1% chance per tick)
        if random.random() < 0.01:
            kube_pod_container_status_restarts_total.labels(namespace=namespace, pod=pod_name, container=container_name).inc()
            kube_deployment_status_replicas_available.labels(namespace=namespace, deployment=deployment_name).set(2)  # Drops to 2 during restart
            time.sleep(5)
            kube_deployment_status_replicas_available.labels(namespace=namespace, deployment=deployment_name).set(3)  # Back to 3
            
        time.sleep(5)

def start_simulation(port=8080):
    start_http_server(port)
    print(f"Pod metrics simulator running on port {port}. Exposing real kube-state-metrics style stats...")
    
    t = threading.Thread(target=simulate_metrics, daemon=True)
    t.start()
    
if __name__ == "__main__":
    start_simulation()
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping pod metrics simulator.")
