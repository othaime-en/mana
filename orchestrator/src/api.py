from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict
import uvicorn
import os
from orchestrator import (
    SelfHealingOrchestrator,
    FailureType,
    DeploymentStatus
)
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

# Initialize orchestrator
orchestrator = SelfHealingOrchestrator(
    redis_host=os.getenv('REDIS_HOST', 'localhost'),
    redis_port=int(os.getenv('REDIS_PORT', 6379)),
    max_retries=int(os.getenv('MAX_RETRIES', 3)),
    rollback_threshold=int(os.getenv('ROLLBACK_THRESHOLD', 2))
)


# Request models
class WebhookPayload(BaseModel):
    """GitHub Actions webhook payload"""
    deployment_id: str
    namespace: str
    app_name: str
    version: str
    status: str
    failure_type: Optional[str] = None
    metadata: Optional[Dict] = {}


class RollbackRequest(BaseModel):
    """Manual rollback request"""
    namespace: str
    deployment_name: str
    target_version: str


class HealthCheckRequest(BaseModel):
    """Health check request"""
    namespace: str
    deployment_name: str
    timeout: int = 300


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


@app.post("/webhook/deployment")
async def deployment_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook endpoint for deployment events from GitHub Actions
    """
    logger.info(f"Received deployment webhook: {payload.deployment_id}")
    
    try:
        # Update metrics
        deployment_counter.labels(
            namespace=payload.namespace,
            status=payload.status
        ).inc()
        
        if payload.status == "failed" and payload.failure_type:
            # Handle failure
            failure_type = FailureType(payload.failure_type)
            
            result = orchestrator.handle_deployment_failure(
                deployment_id=payload.deployment_id,
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version,
                failure_type=failure_type
            )
            
            if result['action'] == 'rollback':
                rollback_counter.labels(
                    namespace=payload.namespace,
                    reason=payload.failure_type
                ).inc()
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "processed",
                    "deployment_id": payload.deployment_id,
                    "action_taken": result
                }
            )
        
        elif payload.status == "success":
            # Track successful deployment
            state = orchestrator.get_deployment_state(payload.deployment_id)
            if state:
                state.status = DeploymentStatus.SUCCESS
                orchestrator.save_deployment_state(state)
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "deployment_id": payload.deployment_id,
                    "message": "Deployment successful"
                }
            )
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "acknowledged",
                "deployment_id": payload.deployment_id
            }
        )
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rollback")
async def manual_rollback(request: RollbackRequest):
    """
    Manual rollback endpoint
    """
    logger.info(f"Manual rollback requested for {request.deployment_name}")
    
    try:
        success = orchestrator.rollback_deployment(
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            target_version=request.target_version
        )
        
        if success:
            rollback_counter.labels(
                namespace=request.namespace,
                reason='manual'
            ).inc()
            
            return {
                "status": "success",
                "message": f"Rolled back to version {request.target_version}"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Rollback failed"
            )
            
    except Exception as e:
        logger.error(f"Error during manual rollback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/health-check")
async def check_health(request: HealthCheckRequest):
    """
    Check deployment health endpoint
    """
    try:
        is_healthy = orchestrator.check_deployment_health(
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            timeout=request.timeout
        )
        
        return {
            "deployment_name": request.deployment_name,
            "namespace": request.namespace,
            "healthy": is_healthy
        }
        
    except Exception as e:
        logger.error(f"Error checking health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/deployment/{deployment_id}")
async def get_deployment(deployment_id: str):
    """
    Get deployment state
    """
    state = orchestrator.get_deployment_state(deployment_id)
    
    if not state:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return {
        "deployment_id": state.deployment_id,
        "namespace": state.namespace,
        "app_name": state.app_name,
        "version": state.version,
        "status": state.status.value,
        "retry_count": state.retry_count,
        "failure_type": state.failure_type.value if state.failure_type else None,
        "timestamp": state.timestamp,
        "metadata": state.metadata
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )