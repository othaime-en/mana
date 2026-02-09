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
from src.utils import get_audit_logger
from prometheus_client import Counter, Histogram, Gauge, make_asgi_app
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

audit_logger = get_audit_logger()

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
health_check_duration = Histogram(
    'health_check_duration_seconds',
    'Health check duration in seconds',
    ['namespace', 'deployment']
)
retry_counter = Counter(
    'retries_total',
    'Total number of deployment retries',
    ['namespace', 'retry_attempt']
)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Initialize orchestrator
orchestrator = SelfHealingOrchestrator(
    redis_host=os.getenv('REDIS_HOST', 'localhost'),
    redis_port=int(os.getenv('REDIS_PORT', 6379)),
    max_retries=int(os.getenv('MAX_RETRIES', 3)),
    rollback_threshold=int(os.getenv('ROLLBACK_THRESHOLD', 2)),
    initial_backoff=float(os.getenv('INITIAL_BACKOFF', 10.0)),
    max_backoff=float(os.getenv('MAX_BACKOFF', 300.0)),
    backoff_multiplier=float(os.getenv('BACKOFF_MULTIPLIER', 2.0)),
    health_check_timeout=int(os.getenv('HEALTH_CHECK_TIMEOUT', 5))
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
        "version": "2.0.0",
        "status": "healthy",
        "features": [
            "Application health endpoint monitoring",
            "Exponential backoff for retries",
            "Comprehensive audit logging",
            "Automatic rollback on failure"
        ]
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
    
    # Log webhook receipt
    audit_logger.log_deployment_received(
        deployment_id=payload.deployment_id,
        namespace=payload.namespace,
        deployment_name=payload.app_name,
        version=payload.version,
        status=payload.status,
        failure_type=payload.failure_type
    )
    
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
            
            # Track retry metrics
            if result['action'] == 'retry':
                retry_counter.labels(
                    namespace=payload.namespace,
                    retry_attempt=result['retry_count']
                ).inc()
            
            # Track rollback metrics
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
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rollback")
async def manual_rollback(request: RollbackRequest):
    """
    Manual rollback endpoint with audit logging
    """
    logger.info(f"Manual rollback requested for {request.deployment_name}")
    
    # Generate deployment ID for manual rollback
    import uuid
    deployment_id = f"manual-rollback-{uuid.uuid4()}"
    
    try:
        success = orchestrator.rollback_deployment(
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            target_version=request.target_version,
            deployment_id=deployment_id
        )
        
        if success:
            rollback_counter.labels(
                namespace=request.namespace,
                reason='manual'
            ).inc()
            
            return {
                "status": "success",
                "deployment_id": deployment_id,
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
    import time
    start_time = time.time()
    
    try:
        is_healthy = orchestrator.check_deployment_health(
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            timeout=request.timeout
        )
        
        duration = time.time() - start_time
        
        # Record health check duration
        health_check_duration.labels(
            namespace=request.namespace,
            deployment=request.deployment_name
        ).observe(duration)
        
        return {
            "deployment_name": request.deployment_name,
            "namespace": request.namespace,
            "healthy": is_healthy,
            "check_duration_seconds": round(duration, 2)
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
        "previous_version": state.previous_version,
        "timestamp": state.timestamp,
        "metadata": state.metadata
    }


@app.get("/metrics/deployment/{namespace}/{deployment_name}")
async def get_metrics(namespace: str, deployment_name: str):
    """
    Get deployment metrics
    """
    try:
        metrics = orchestrator.get_deployment_metrics(namespace, deployment_name)
        return metrics
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """
    Get orchestrator configuration
    """
    return {
        "max_retries": orchestrator.max_retries,
        "rollback_threshold": orchestrator.rollback_threshold,
        "initial_backoff": orchestrator.initial_backoff,
        "max_backoff": orchestrator.max_backoff,
        "backoff_multiplier": orchestrator.backoff_multiplier,
        "health_check_timeout": orchestrator.health_check_timeout
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )