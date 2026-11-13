from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum
from typing import Optional, Dict, List
import asyncio
import json
import uvicorn
import os
import redis
from src.orchestrator import (
    DeploymentStatus,
    SelfHealingOrchestrator,
    FailureType
)
from src.auth import require_api_key, require_signed_webhook, warn_if_disabled
from src.config import get_config as load_config
from src.deliveries import Deliveries
from src.jobs import Scheduler
from src.logging_config import configure_logging
from src.utils import get_audit_logger
from src.watcher import Watcher
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from src import metrics
import logging

_config = load_config()
configure_logging(_config.log_level, _config.enable_audit_logging)
logger = logging.getLogger(__name__)

audit_logger = get_audit_logger()
warn_if_disabled(_config)

# Initialize orchestrator with configuration from config.py
orchestrator = SelfHealingOrchestrator(use_config=True)


def build_scheduler() -> Scheduler:
    """The job scheduler that runs retries and rollbacks on this replica"""
    return Scheduler(
        queue=orchestrator.jobs,
        handlers=orchestrator.job_handlers(),
        on_dead=orchestrator.handle_dead_job,
        retry_delay=orchestrator.calculate_backoff,
        lease_seconds=_config.job_lease_seconds,
        poll_seconds=_config.scheduler_poll_seconds,
        max_concurrency=_config.scheduler_concurrency,
        max_attempts=_config.job_max_attempts
    )


def build_watcher() -> Optional[Watcher]:
    """The cluster-side watcher, when WATCHER_ENABLED is set"""
    if not _config.watcher_enabled:
        return None
    return Watcher(
        orchestrator,
        _config.watched_namespaces(),
        _config.watcher_interval_seconds,
        _config.watcher_stuck_seconds
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the scheduler for the life of the process; drain it on shutdown"""
    scheduler = build_scheduler()
    worker = asyncio.create_task(scheduler.run(), name="scheduler")
    watcher = build_watcher()
    watching = asyncio.create_task(watcher.run(), name="watcher") if watcher else None
    _app.state.draining = False
    try:
        yield
    finally:
        _app.state.draining = True
        if watcher:
            watcher.stop()
            await watching
        await scheduler.stop(_config.shutdown_grace_seconds)
        await worker


app = FastAPI(
    title="Self-Healing Orchestrator API",
    description="API for managing self-healing CI/CD deployments",
    version="1.0.0",
    lifespan=lifespan
)
REGISTRY.register(metrics.ActiveDeploymentsCollector(orchestrator.active_deployment_counts))
REGISTRY.register(metrics.SchedulerCollector(lambda: orchestrator.jobs.stats()))


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


@app.get("/livez")
async def livez():
    """
    Liveness probe: the process is up and its event loop is responding

    Depends on nothing else, so a Redis or Kubernetes outage never makes the
    kubelet restart a healthy process.
    """
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    """
    Readiness probe: this replica can do its work

    Needs Redis, which holds all state and the job queue. Fails while the
    replica drains on shutdown so traffic moves to the others first.
    """
    if getattr(app.state, "draining", False):
        return JSONResponse(status_code=503, content={"status": "draining"})
    try:
        orchestrator.redis_client.ping()
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "redis": "unhealthy"}
        )
    return {"status": "ready"}


@app.get("/health")
def health():
    """Dependency report; answers 503 when Redis or Kubernetes is unreachable"""
    try:
        # Check Redis connectivity
        orchestrator.redis_client.ping()
        redis_healthy = True
    except Exception:
        redis_healthy = False
    
    try:
        # The version endpoint needs no cluster-scoped permission
        orchestrator.k8s_version.get_code()
        k8s_healthy = True
    except Exception:
        k8s_healthy = False
    
    overall_healthy = redis_healthy and k8s_healthy
    
    return JSONResponse(
        status_code=200 if overall_healthy else 503,
        content={
            "status": "healthy" if overall_healthy else "degraded",
            "redis": "healthy" if redis_healthy else "unhealthy",
            "kubernetes": "healthy" if k8s_healthy else "unhealthy",
            "github_integration": bool(
                orchestrator.github_token and orchestrator.github_repo
            )
        }
    )


@app.post("/webhook/deployment", dependencies=[Depends(require_signed_webhook)])
async def deployment_webhook(
    payload: WebhookPayload,
    x_mana_signature: Optional[str] = Header(default=None)
):
    """
    Webhook endpoint for deployment events from GitHub Actions

    A request that was already seen, whether replayed, retried by the sender or
    delivered to two replicas, is answered with the first response and not
    processed again. Without a signature (AUTH_DISABLED) there is nothing to
    identify a delivery by, so every request is processed.
    """
    if not x_mana_signature:
        return await process_deployment_event(payload)
    
    deliveries = Deliveries(orchestrator.redis_client)
    try:
        claim = await asyncio.to_thread(deliveries.begin, x_mana_signature)
    except redis.RedisError:
        logger.warning("Could not check for duplicate deliveries; processing anyway", exc_info=True)
        return await process_deployment_event(payload)
    
    if not claim.is_new:
        outcome = "replayed" if claim.state == "done" else "in_flight"
        metrics.webhook_duplicates_total.labels(outcome=outcome).inc()
        logger.warning(
            f"Dropped a duplicate delivery for {payload.deployment_id} ({outcome})"
        )
        if claim.state == "done":
            return JSONResponse(
                status_code=claim.status_code,
                content=claim.body,
                headers={"X-Mana-Duplicate": "true"}
            )
        raise HTTPException(
            status_code=409,
            detail="This delivery is already being processed"
        )
    
    try:
        response = await process_deployment_event(payload)
    except BaseException:
        await asyncio.to_thread(deliveries.abandon, claim)
        raise
    
    try:
        await asyncio.to_thread(
            deliveries.finish, claim, response.status_code, json.loads(response.body)
        )
    except redis.RedisError:
        logger.warning("Could not record the delivery result; copies may be reprocessed", exc_info=True)
    return response


async def process_deployment_event(payload: WebhookPayload) -> JSONResponse:
    """Record a deployment event and schedule recovery when it failed"""
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
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "processed",
                    "deployment_id": payload.deployment_id,
                    "action_taken": result
                }
            )
        
        elif payload.status is WebhookStatus.SUCCESS:
            transition = await asyncio.to_thread(
                orchestrator.handle_deployment_success,
                deployment_id=payload.deployment_id,
                namespace=payload.namespace,
                deployment_name=payload.app_name,
                version=payload.version,
                metadata=metadata
            )
            
            if transition.state.status is DeploymentStatus.VERIFYING:
                settings = orchestrator.policy.verification
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "verifying",
                        "deployment_id": payload.deployment_id,
                        "message": (
                            f"Deployment healthy - verifying against Prometheus for up to "
                            f"{settings.bake_seconds}s before it is saved as a rollback target"
                        ),
                        "bake_seconds": settings.bake_seconds
                    }
                )
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "deployment_id": payload.deployment_id,
                    "message": "Deployment successful - version saved for future rollbacks"
                }
            )
        
        denied = await asyncio.to_thread(
            orchestrator.check_version_denied,
            payload.deployment_id,
            payload.namespace,
            payload.app_name,
            payload.version,
            metadata
        )
        if denied:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "denied",
                    "deployment_id": payload.deployment_id,
                    "message": (
                        f"Version {payload.version} was rolled back earlier and is on the "
                        f"deny-list; do not deploy it"
                    ),
                    "reason": denied["reason"],
                    "denied_since": denied["since"]
                }
            )
        
        try:
            await asyncio.to_thread(
                orchestrator.record_deployment_started,
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
    
    lock = await asyncio.to_thread(
        orchestrator.locks.acquire, request.namespace, request.deployment_name
    )
    if lock is None:
        raise HTTPException(
            status_code=409,
            detail=f"A rollback of {request.namespace}/{request.deployment_name} is already in progress"
        )
    
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
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during manual rollback: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await asyncio.to_thread(lock.release)


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
    state = await asyncio.to_thread(orchestrator.get_deployment_state, deployment_id)
    
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
        states = await asyncio.to_thread(
            orchestrator.get_recent_deployments, namespace=namespace, limit=limit
        )
        
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
    """Replica status of a Kubernetes Deployment, plus the circuit breaker and deny-list"""
    try:
        status = await asyncio.to_thread(
            orchestrator.get_deployment_metrics, namespace, deployment_name
        )
        automation = await asyncio.to_thread(
            orchestrator.get_automation_status, namespace, deployment_name
        )
        return {**status, "automation": automation}
    except Exception as e:
        logger.error(f"Error getting deployment status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete(
    "/denied-versions/{namespace}/{deployment_name}/{version}",
    dependencies=[Depends(require_api_key)]
)
async def allow_denied_version(namespace: str, deployment_name: str, version: str):
    """Take a version off the deny-list so a pipeline may deploy it again"""
    try:
        removed = await asyncio.to_thread(
            orchestrator.guards.allow, namespace, deployment_name, version
        )
    except redis.RedisError as e:
        logger.error(f"Could not update the deny-list: {e}")
        raise HTTPException(status_code=503, detail="Redis is unavailable")
    if not removed:
        raise HTTPException(status_code=404, detail=f"{version} is not on the deny-list")
    return {"status": "allowed", "namespace": namespace, "deployment_name": deployment_name, "version": version}


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
        ),
        "policy": orchestrator.policy_info()
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        # The container must listen on all interfaces; NetworkPolicy limits callers
        host="0.0.0.0",  # nosec B104
        port=port,
        log_config=None,
        log_level=_config.log_level.lower()
    )