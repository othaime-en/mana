from fastapi import FastAPI
import uvicorn
import os
from prometheus_client import Counter, Histogram, Gauge, make_asgi_app
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Self-Healing Orchestrator API",
    description="API for managing self-healing CI/CD deployments",
    version="1.0.0"
)

# Prometheus metrics
deployment_counter = Counter(
    'deployments_total',
    'Total number of deployments',
    ['namespace', 'status']
)
rollback_counter = Counter(
    'rollbacks_total',
    'Total number of rollbacks',
    ['namespace', 'reason']
)
deployment_duration = Histogram(
    'deployment_duration_seconds',
    'Deployment duration in seconds',
    ['namespace']
)
active_deployments = Gauge(
    'active_deployments',
    'Number of active deployments',
    ['namespace']
)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# API Endpoints
@app.get("/")
def root():
    """Root endpoint"""
    return {
        "service": "Self-Healing Orchestrator",
        "version": "1.0.0",
        "status": "healthy"
    }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )