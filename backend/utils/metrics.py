from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

# Prometheus Metrics Definitions
HTTP_REQUESTS_TOTAL = Counter(
    "learnmesh_http_requests_total",
    "Total count of HTTP requests handled by LearnMesh",
    ["method", "endpoint", "status_code"]
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "learnmesh_http_request_duration_seconds",
    "Duration of HTTP requests in seconds",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

MEMORIES_RETAINED_TOTAL = Counter(
    "learnmesh_memories_retained_total",
    "Total number of organizational memories retained",
    ["mode"]  # "live" or "degraded_local"
)

MEMORIES_RECALLED_TOTAL = Counter(
    "learnmesh_memories_recalled_total",
    "Total number of times memories were recalled for agent interactions"
)

CONTRADICTIONS_DETECTED_TOTAL = Counter(
    "learnmesh_contradictions_detected_total",
    "Total number of policy contradictions surfaced"
)

CIRCUIT_BREAKER_TRIPS_TOTAL = Counter(
    "learnmesh_circuit_breaker_trips_total",
    "Total times a circuit breaker has tripped to OPEN",
    ["service"]
)

ACTIVE_JOBS_GAUGE = Gauge(
    "learnmesh_active_background_jobs",
    "Number of currently running background jobs"
)

def get_metrics_response() -> Response:
    """Returns Prometheus formatted metrics output."""
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
