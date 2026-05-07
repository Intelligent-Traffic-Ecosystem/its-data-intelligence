from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

router = APIRouter()

# Define Prometheus metrics
REQUEST_COUNT = Counter(#count total AP request
    "b2_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(#measure tme toget for request
    "b2_api_request_latency_seconds",
    "API request latency",
    ["endpoint"],
)
PROCESSING_ERRORS = Counter(#count systemerrors
    "b2_processing_errors_total",
    "Total processing errors",
    ["type"],
)


@router.get("/metrics")
def prometheus_metrics():
    """Prometheus scrape endpoint for B4 monitoring."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
