from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum
from typing import Optional, Dict, List
import asyncio
import uvicorn
import os
import redis
from src.orchestrator import (
    SelfHealingOrchestrator,
    FailureType
)
from src.auth import require_api_key, require_signed_webhook, warn_if_disabled
from src.config import get_config
from src.logging_config import configure_logging
from src.utils import get_audit_logger
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from src import metrics
import logging

_config = get_config()
configure_logging(_config.log_level, _config.enable_audit_logging)
logger = logging.getLogger(__name__)

audit_logger = get_audit_logger()
warn_if_disabled(_config)

app = FastAPI(
    title="Self-Healing Orchestrator API",
    description="API for managing self-healing CI/CD deployments",
    version="1.0.0"
)

# Initialize orchestrator with configuration from config.py
orchestrator = SelfHealingOrchestrator(use_config=True)
REGISTRY.register(metrics.ActiveDeploymentsCollector(orchestrator.active_deployment_counts))


@app.middleware("http")
async def count_webhook_requests(request, call_next):
    """Count webhook requests and their error responses, including 422s"""
    if not request.url.path.startswith("/webhook/"):
        return await call_next(request)
    
    try:
        response = await call_next(request)
    except Exception:
        metrics.record_webhook(request.url.path, 500)
        raise
    metrics.record_webhook(request.url.path, response.status_code)
    return response


# Request models
class WebhookStatus(str, Enum):
    """Deployment states a pipeline may report"""
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"


class WebhookPayload(BaseModel):
    """
    GitHub Actions webhook payload

    Requires workflow_run_id in metadata for the retry mechanism.
    Unknown status or failure_type values are rejected with HTTP 422.
    """
    deployment_id: str
    namespace: str
    app_name: str
    version: str
    status: WebhookStatus
    failure_type: Optional[FailureType] = None
    metadata: Optional[Dict] = Field(default_factory=dict)

    @field_validator("failure_type", mode="before")
    @classmethod
    def blank_failure_type_means_none(cls, value):
        """The deploy workflow sends an empty string when nothing failed"""
        return None if value == "" else value

    @model_validator(mode="after")
    def failed_requires_failure_type(self) -> "WebhookPayload":
        if self.status is WebhookStatus.FAILED and self.failure_type is None:
            raise ValueError("failure_type is required when status is failed")
        return self


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


@app.post("/webhook/deployment", dependencies=[Depends(require_signed_webhook)])
async def deployment_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook endpoint for deployment events from GitHub Actions
    Stores workflow_run_id in metadata for retry mechanism
    """
    logger.info(f"Received deployment webhook: {payload.deployment_id}")
    
    metadata = payload.metadata or {}
    
    # Validate workflow_run_id is present for failed deployments
    if payload.status is WebhookStatus.FAILED and not metadata.get('workflow_run_id'):
        logger.warning(
            "Webhook payload missing workflow_run_id - retry functionality will be limited"
        )
    
    # Log webhook receipt
    audit_logger.log_deployment_received(
        deployment_id=payload.deployment_id,
        namespace=payload.namespace,
        deployment_name=payload.app_name,
        version=payload.version,
        status=payload.status.value,
        failure_type=payload.failure_type.value if payload.failure_type else None
    )
    
    try:
        if payload.status is WebhookStatus.FAILED:
            failure_type = payload.failure_type
            
            result = await orchestrator.handle_deployment_failure(
                deployment_id=payload.deployment_id,
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version,
                failure_type=failure_type,
                metadata=metadata
            )
            
            # Track retry metrics
            if result['action'] == 'retry':
                metrics.retries_total.labels(
                    namespace=payload.namespace,
                    retry_attempt=result['retry_count']
                ).inc()
            
            # Track rollback metrics
            if result['action'] == 'rollback':
                metrics.rollbacks_total.labels(
                    namespace=payload.namespace,
                    reason=failure_type.value
                ).inc()
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "processed",
                    "deployment_id": payload.deployment_id,
                    "action_taken": result
                }
            )
        
        elif payload.status is WebhookStatus.SUCCESS:
            orchestrator.record_deployment_succeeded(
                deployment_id=payload.deployment_id,
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version,
                metadata=metadata
            )
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "deployment_id": payload.deployment_id,
                    "message": "Deployment successful - version saved for future rollbacks"
                }
            )
        
        try:
            orchestrator.record_deployment_started(
                deployment_id=payload.deployment_id,
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version,
                metadata=metadata
            )
        except redis.RedisError:
            logger.error(
                "Could not persist in_progress state for %s",
                payload.deployment_id,
                exc_info=True
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


@app.post("/rollback", dependencies=[Depends(require_api_key)])
async def manual_rollback(request: RollbackRequest):
    """Manual rollback endpoint with audit logging"""
    logger.info(f"Manual rollback requested for {request.deployment_name}")
    
    # Generate deployment ID for manual rollback
    import uuid
    deployment_id = f"manual-rollback-{uuid.uuid4()}"
    
    try:
        success = await asyncio.to_thread(
            orchestrator.rollback_deployment,
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            target_version=request.target_version,
            deployment_id=deployment_id,
            reason="manual rollback requested"
        )
        
        if success:
            metrics.rollbacks_total.labels(
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


@app.post("/health-check", dependencies=[Depends(require_api_key)])
async def check_health(request: HealthCheckRequest):
    """Check deployment health endpoint"""
    import time
    start_time = time.time()
    
    try:
        is_healthy = await asyncio.to_thread(
            orchestrator.check_deployment_health,
            namespace=request.namespace,
            deployment_name=request.deployment_name,
            timeout=request.timeout
        )
        
        duration = time.time() - start_time
        
        return {
            "deployment_name": request.deployment_name,
            "namespace": request.namespace,
            "healthy": is_healthy,
            "check_duration_seconds": round(duration, 2)
        }
        
    except Exception as e:
        logger.error(f"Error checking health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/deployment/{deployment_id}", dependencies=[Depends(require_api_key)])
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
        "started_at": state.started_at,
        "first_failure_at": state.first_failure_at,
        "finished_at": state.finished_at,
        "metadata": state.metadata
    }


@app.get("/deployments/recent", dependencies=[Depends(require_api_key)])
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


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus exposition; rendered in a thread because collectors read Redis"""
    body = await asyncio.to_thread(generate_latest)
    return Response(body, media_type=CONTENT_TYPE_LATEST)


@app.get("/deployments/{namespace}/{deployment_name}/status", dependencies=[Depends(require_api_key)])
async def get_deployment_status(namespace: str, deployment_name: str):
    """Replica status of a Kubernetes Deployment"""
    try:
        return await asyncio.to_thread(
            orchestrator.get_deployment_metrics, namespace, deployment_name
        )
    except Exception as e:
        logger.error(f"Error getting deployment status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config", dependencies=[Depends(require_api_key)])
async def get_config():
    """Get orchestrator configuration"""
    return {
        "max_retries": orchestrator.max_retries,
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
        log_config=None,
        log_level=_config.log_level.lower()
    )