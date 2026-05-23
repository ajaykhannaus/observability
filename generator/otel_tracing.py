import os
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_tracing_initialized = False
tracer = trace.get_tracer("ai-telemetry-poc")

def setup_tracing():
    global _tracing_initialized, tracer
    if _tracing_initialized:
        return

    try:
        from opentelemetry.exporter.jaeger.thrift import JaegerExporter
        _JAEGER_AVAILABLE = True
    except ImportError:
        _JAEGER_AVAILABLE = False
        logger.warning("opentelemetry-exporter-jaeger not found. Spans will not be exported.")

    service_name = os.getenv("OTEL_SERVICE_NAME", "ai-telemetry-poc")
    environment = os.getenv("ENVIRONMENT", "poc")

    resource = Resource.create({
        "service.name": service_name,
        "deployment.environment": environment
    })

    provider = TracerProvider(resource=resource)
    
    if _JAEGER_AVAILABLE:
        # Export to local Jaeger by default (agent at localhost:6831)
        jaeger_exporter = JaegerExporter(
            agent_host_name="localhost",
            agent_port=6831,
        )
        span_processor = BatchSpanProcessor(jaeger_exporter)
        provider.add_span_processor(span_processor)
        logger.info("Jaeger trace exporter configured for localhost:6831")

    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(service_name)
    _tracing_initialized = True
    logger.info("OTel tracing ready")

def get_tracer():
    if not _tracing_initialized:
        setup_tracing()
    return tracer
