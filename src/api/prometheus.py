from fastapi import APIRouter, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

router = APIRouter()

# Define Prometheus metrics
REQUEST_COUNT = Counter(
    "b2_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "b2_api_request_latency_seconds",
    "API request latency",
    ["endpoint"],
)
PROCESSING_ERRORS = Counter(
    "b2_processing_errors_total",
    "Total processing errors",
    ["type"],
)


@router.get("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint for B4 monitoring."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
