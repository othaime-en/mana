from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, List
import uvicorn
import os
from src.orchestrator import (
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

# Initialize orchestrator with configuration from config.py
orchestrator = SelfHealingOrchestrator(use_config=True)


# Request models
class WebhookPayload(BaseModel):
    """
    GitHub Actions webhook payload
    
    Requires workflow_run_id for retry mechanism
    """
    deployment_id: str
    namespace: str
    app_name: str
    version: str
    status: str
    failure_type: Optional[str] = None
    metadata: Optional[Dict] = {}  # Should include workflow_run_id


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
        "status": "healthy",
        "features": [
            "Application health endpoint monitoring",
            "Exponential backoff for retries",
            "GitHub Actions retry integration",
            "Comprehensive audit logging",
            "Automatic rollback on failure",
            "Previous version tracking"
        ],
        "github_integration": bool(
            orchestrator.github_token and orchestrator.github_repo
        )
    }


@app.get("/health")
def health():
    """Health check endpoint"""
    try:
        # Check Redis connectivity
        orchestrator.redis_client.ping()
        redis_healthy = True
    except:
        redis_healthy = False
    
    try:
        # Check Kubernetes connectivity
        orchestrator.k8s_core.list_namespace(limit=1)
        k8s_healthy = True
    except:
        k8s_healthy = False
    
    overall_healthy = redis_healthy and k8s_healthy
    
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "redis": "healthy" if redis_healthy else "unhealthy",
        "kubernetes": "healthy" if k8s_healthy else "unhealthy",
        "github_integration": bool(
            orchestrator.github_token and orchestrator.github_repo
        )
    }


@app.post("/webhook/deployment")
async def deployment_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook endpoint for deployment events from GitHub Actions
    Stores workflow_run_id in metadata for retry mechanism
    """
    logger.info(f"Received deployment webhook: {payload.deployment_id}")
    
    # Validate workflow_run_id is present for failed deployments
    if payload.status == "failed" and not payload.metadata.get('workflow_run_id'):
        logger.warning(
            "Webhook payload missing workflow_run_id - retry functionality will be limited"
        )
    
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
            
            # Create or update deployment state with metadata
            state = orchestrator.get_deployment_state(payload.deployment_id)
            if state:
                # Update existing state with new metadata
                state.metadata.update(payload.metadata)
                orchestrator.save_deployment_state(state)
            
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
            # Track successful deployment for rollback reference
            state = orchestrator.get_deployment_state(payload.deployment_id)
            if state:
                state.status = DeploymentStatus.SUCCESS
                orchestrator.save_deployment_state(state)
            
            # Store as rollback candidate
            orchestrator.save_successful_deployment_version(
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version
            )
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "deployment_id": payload.deployment_id,
                    "message": "Deployment successful - version saved for future rollbacks"
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
    """Manual rollback endpoint with audit logging"""
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
    """Check deployment health endpoint"""
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
    """Get deployment state"""
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


@app.get("/deployments/recent")
async def get_recent_deployments(namespace: Optional[str] = None, limit: int = 10):
    """
    Get recent deployment states
    """
    try:
        states = orchestrator.get_recent_deployments(namespace=namespace, limit=limit)
        
        return {
            "count": len(states),
            "deployments": [
                {
                    "deployment_id": state.deployment_id,
                    "namespace": state.namespace,
                    "app_name": state.app_name,
                    "version": state.version,
                    "status": state.status.value,
                    "retry_count": state.retry_count,
                    "timestamp": state.timestamp
                }
                for state in states
            ]
        }
    except Exception as e:
        logger.error(f"Error getting recent deployments: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics/deployment/{namespace}/{deployment_name}")
async def get_metrics(namespace: str, deployment_name: str):
    """Get deployment metrics"""
    try:
        metrics = orchestrator.get_deployment_metrics(namespace, deployment_name)
        return metrics
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """Get orchestrator configuration"""
    return {
        "max_retries": orchestrator.max_retries,
        "rollback_threshold": orchestrator.rollback_threshold,
        "initial_backoff": orchestrator.initial_backoff,
        "max_backoff": orchestrator.max_backoff,
        "backoff_multiplier": orchestrator.backoff_multiplier,
        "health_check_timeout": orchestrator.health_check_timeout,
        "max_app_health_failures": orchestrator.max_app_health_failures,
        "github_integration_enabled": bool(
            orchestrator.github_token and orchestrator.github_repo
        )
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )