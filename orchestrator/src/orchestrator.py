"""
Self-healing orchestrator for CI/CD pipeline
Monitors deployments and automatically handles failures with intelligent recovery
"""

import logging
import time
import requests
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict, fields
from enum import Enum
import redis
import json
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

from src import metrics
from src.utils import get_audit_logger, AuditAction, split_image_reference, with_tag
from src.config import get_config


logger = logging.getLogger(__name__)

audit_logger = get_audit_logger()


class DeploymentStatus(Enum):
    """Deployment status enum"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    RETRY_SCHEDULED = "retry_scheduled"


class FailureType(Enum):
    """Types of failures"""
    BUILD_FAILURE = "build_failure"
    TEST_FAILURE = "test_failure"
    DEPLOYMENT_FAILURE = "deployment_failure"
    HEALTH_CHECK_FAILURE = "health_check_failure"
    SMOKE_TEST_FAILURE = "smoke_test_failure"
    TIMEOUT = "timeout"


NON_ROLLBACK_FAILURES = frozenset({
    FailureType.BUILD_FAILURE,
    FailureType.TEST_FAILURE,
})

ACTIVE_STATUSES = frozenset({
    DeploymentStatus.IN_PROGRESS,
    DeploymentStatus.RETRY_SCHEDULED,
    DeploymentStatus.ROLLING_BACK,
})

TERMINAL_STATUSES = frozenset({
    DeploymentStatus.SUCCESS,
    DeploymentStatus.FAILED,
    DeploymentStatus.ROLLED_BACK,
})

STATE_TTL_SECONDS = 86400
ACTIVE_INDEX_KEY = "mana:active"


class ContainerNotFoundError(Exception):
    """The Deployment has no container Mana can identify as the application"""


class RollbackError(Exception):
    """A rollback patch was accepted but did not produce the expected image"""


def rollout_complete(deployment, generation: int) -> bool:
    """
    Whether the Deployment finished rolling out the given generation

    Mirrors kubectl rollout status: the controller has observed the spec, all
    replicas are updated, and no replicas from older revisions remain.
    """
    status = deployment.status
    updated = status.updated_replicas or 0
    return (
        (status.observed_generation or 0) >= generation
        and updated == deployment.spec.replicas
        and (status.replicas or 0) == updated
    )


def find_container(deployment, deployment_name: str):
    """
    Return the application container of a Deployment

    Matches the container named like the Deployment, or the only container
    when there is exactly one.
    """
    containers = deployment.spec.template.spec.containers or []
    for container in containers:
        if container.name == deployment_name:
            return container
    if len(containers) == 1:
        return containers[0]
    names = [container.name for container in containers]
    raise ContainerNotFoundError(
        f"No container named {deployment_name} in deployment (found {names})"
    )


@dataclass
class DeploymentState:
    """Deployment state tracking"""
    deployment_id: str
    namespace: str
    app_name: str
    version: str
    status: DeploymentStatus
    previous_version: Optional[str]
    retry_count: int
    failure_type: Optional[FailureType]
    timestamp: float
    metadata: Dict
    started_at: Optional[float] = None
    first_failure_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class StateTransition:
    """A saved state together with the status it replaced (None if new)"""
    state: DeploymentState
    previous_status: Optional[DeploymentStatus]


class SelfHealingOrchestrator:
    """
    Self-healing orchestrator for CI/CD pipeline
    Monitors deployments and automatically handles failures
    """
    
    def __init__(self, use_config: bool = True):
        """
        Initialize orchestrator with validation
        
        Args:
            use_config: If True, loads configuration from config.py (recommended).
                       If False, uses default values for testing.
        """
        if use_config:
            cfg = get_config()
            cfg.validate()  # Validate configuration
            
            # Redis configuration
            self.redis_client = metrics.CountingRedis(
                redis.Redis(**cfg.redis_client_kwargs())
            )
            
            # Validate Redis connection
            try:
                self.redis_client.ping()
                logger.info("Redis connection established")
            except redis.ConnectionError as e:
                logger.critical(f"Cannot connect to Redis: {e}")
                raise RuntimeError("Redis connection failed - orchestrator cannot start") from e
            
            # Retry/Rollback configuration
            self.max_retries = cfg.max_retries
            
            # Exponential backoff configuration
            self.initial_backoff = cfg.initial_backoff
            self.max_backoff = cfg.max_backoff
            self.backoff_multiplier = cfg.backoff_multiplier
            
            # Health check configuration
            self.health_check_timeout = cfg.health_check_timeout
            self.health_check_port = cfg.health_check_port
            self.health_check_path = cfg.health_check_path
            self.max_app_health_failures = 3  # Allow 3 consecutive app health failures
            
        else:
            # Fallback for testing or manual initialization
            self.redis_client = metrics.CountingRedis(redis.Redis(
                host='localhost',
                port=6379,
                decode_responses=True
            ))
            self.max_retries = 3
            self.initial_backoff = 10.0
            self.max_backoff = 300.0
            self.backoff_multiplier = 2.0
            self.health_check_timeout = 5
            self.health_check_port = 5000
            self.health_check_path = '/health'
            self.max_app_health_failures = 3
        
        # GitHub integration for retry mechanism
        import os
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.github_repo = os.getenv('GITHUB_REPO')  # Format: "owner/repo"
        
        if not self.github_token:
            logger.warning(
                "GITHUB_TOKEN not set - retry functionality will be limited. "
                "Set GITHUB_TOKEN environment variable to enable automatic retries."
            )
        if not self.github_repo:
            logger.warning(
                "GITHUB_REPO not set - retry functionality will be limited. "
                "Set GITHUB_REPO environment variable (format: 'owner/repo')."
            )
        
        # Load Kubernetes config
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except:
            try:
                k8s_config.load_kube_config()
                logger.info("Loaded local kubeconfig")
            except Exception as e:
                logger.critical(f"Cannot load Kubernetes config: {e}")
                raise RuntimeError("Kubernetes config failed - orchestrator cannot start") from e
        
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
        
        logger.info(
            "Self-Healing Orchestrator initialized successfully",
            extra={
                "max_retries": self.max_retries,
                "initial_backoff": self.initial_backoff,
                "max_backoff": self.max_backoff,
                "github_integration": bool(self.github_token and self.github_repo)
            }
        )
    
    def calculate_backoff(self, retry_count: int) -> float:
        """
        Calculate exponential backoff delay
        
        Args:
            retry_count: Current retry attempt number (1-indexed)
            
        Returns:
            Backoff delay in seconds
        """
        backoff = self.initial_backoff * (self.backoff_multiplier ** (retry_count - 1))
        return min(backoff, self.max_backoff)
    
    def check_application_health(
        self,
        namespace: str,
        deployment_name: str,
        port: Optional[int] = None,
        health_path: Optional[str] = None
    ) -> bool:
        """
        Check application health by calling its health endpoint
        
        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of deployment
            port: Application port (uses config if not specified)
            health_path: Health check endpoint path (uses config if not specified)
            
        Returns:
            True if at least one pod is healthy, False otherwise
        """
        # Use configured values if not provided
        port = port or self.health_check_port
        health_path = health_path or self.health_check_path
        
        try:
            # Get pods for deployment
            pods = self.k8s_core.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app={deployment_name}"
            )
            
            if not pods.items:
                logger.warning(f"No pods found for deployment {deployment_name}")
                return False
            
            # Check health of each pod
            healthy_pods = 0
            total_running_pods = 0
            
            for pod in pods.items:
                if pod.status.phase != "Running":
                    continue
                
                total_running_pods += 1
                pod_ip = pod.status.pod_ip
                if not pod_ip:
                    continue
                
                try:
                    # Call health endpoint
                    health_url = f"http://{pod_ip}:{port}{health_path}"
                    response = requests.get(
                        health_url,
                        timeout=self.health_check_timeout
                    )
                    
                    if response.status_code == 200:
                        # Verify response has expected structure
                        try:
                            data = response.json()
                            if data.get('status') == 'healthy':
                                healthy_pods += 1
                                logger.debug(
                                    f"Pod {pod.metadata.name} health check passed"
                                )
                            else:
                                logger.warning(
                                    f"Pod {pod.metadata.name} returned unhealthy status: {data}"
                                )
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Pod {pod.metadata.name} health endpoint returned "
                                f"non-JSON response"
                            )
                    else:
                        logger.warning(
                            f"Pod {pod.metadata.name} health check failed "
                            f"with status {response.status_code}"
                        )
                
                except requests.exceptions.Timeout:
                    logger.warning(
                        f"Health check timeout for pod {pod.metadata.name}"
                    )
                    continue
                except requests.exceptions.RequestException as e:
                    logger.warning(
                        f"Failed to reach pod {pod.metadata.name}: {e}"
                    )
                    continue
            
            # Log results
            logger.info(
                f"Application health check results: "
                f"{healthy_pods}/{total_running_pods} pods healthy"
            )
            
            # Consider deployment healthy if at least one pod is healthy
            return healthy_pods > 0
            
        except Exception as e:
            logger.error(
                f"Error checking application health: {e}",
                exc_info=True
            )
            return False
    
    def save_deployment_state(self, state: DeploymentState) -> None:
        """
        Save deployment state to Redis with proper Enum serialization

        Stamps finished_at on the first save in a terminal status and keeps the
        active-deployment index in step with the status.
        """
        key = f"deployment:{state.deployment_id}"
        
        if state.status in TERMINAL_STATUSES and state.finished_at is None:
            state.finished_at = time.time()
        
        # Convert state to dict and explicitly handle Enums
        state_dict = asdict(state)
        state_dict['status'] = state.status.value  # Convert Enum to string
        if state.failure_type:
            state_dict['failure_type'] = state.failure_type.value  # Convert Enum to string
        
        try:
            previous = self.redis_client.set(
                key,
                json.dumps(state_dict),
                ex=STATE_TTL_SECONDS,
                get=True
            )
            
            audit_logger.log_event(
                action=AuditAction.STATE_SAVED,
                deployment_id=state.deployment_id,
                namespace=state.namespace,
                deployment_name=state.app_name,
                version=state.version,
                success=True,
                details={
                    "status": state.status.value,
                    "retry_count": state.retry_count
                }
            )
            
            logger.debug(f"Saved deployment state: {state.deployment_id}")
            
        except Exception as e:
            logger.error(f"Failed to save deployment state: {e}", exc_info=True)
            raise
        
        self._update_active_index(state)
        self._record_outcome(state, previous)
    
    @staticmethod
    def _status_of(raw) -> Optional[DeploymentStatus]:
        """Status stored in a saved state document, or None if unreadable"""
        try:
            return DeploymentStatus(json.loads(raw)['status'])
        except (TypeError, ValueError, KeyError):
            return None
    
    def _record_outcome(self, state: DeploymentState, previous_raw) -> None:
        """
        Count a deployment when it moves into a terminal status

        SET ... GET hands back the document it replaced, so the transition is
        detected in the same round trip as the write. A duplicate delivery
        (terminal to the same terminal status) is not counted again, but a
        re-run that reopens the deployment and settles again is.
        """
        if state.status not in TERMINAL_STATUSES:
            return
        if self._status_of(previous_raw) in TERMINAL_STATUSES:
            return
        metrics.record_outcome(
            namespace=state.namespace,
            app=state.app_name,
            status=state.status.value,
            started_at=state.started_at,
            first_failure_at=state.first_failure_at,
            finished_at=state.finished_at
        )
    
    def _update_active_index(self, state: DeploymentState) -> None:
        """Track deployments that have not reached a terminal status"""
        member = f"{state.namespace}|{state.deployment_id}"
        try:
            if state.status in ACTIVE_STATUSES:
                score = state.started_at or state.timestamp
                self.redis_client.zadd(ACTIVE_INDEX_KEY, {member: score})
            else:
                self.redis_client.zrem(ACTIVE_INDEX_KEY, member)
        except redis.RedisError as e:
            logger.warning(f"Could not update the active deployment index: {e}")
    
    def active_deployment_counts(self) -> Dict[str, int]:
        """
        Count deployments that are still in flight, by namespace

        Entries older than the state TTL are dropped first, so a workflow that
        was cancelled without a terminal webhook does not stay active forever.
        """
        self.redis_client.zremrangebyscore(
            ACTIVE_INDEX_KEY, "-inf", time.time() - STATE_TTL_SECONDS
        )
        counts: Dict[str, int] = {}
        for member in self.redis_client.zrange(ACTIVE_INDEX_KEY, 0, -1):
            namespace = member.partition("|")[0]
            counts[namespace] = counts.get(namespace, 0) + 1
        return counts
    
    def record_deployment_started(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        metadata: Optional[Dict] = None
    ) -> StateTransition:
        """
        Persist an in_progress event

        A re-run of a deployment that already has state reopens it and keeps
        started_at, retry_count and previous_version, so the duration covers
        every attempt. previous_version is resolved at failure time, because
        the ReplicaSet fallback is only correct once the failed version exists.
        """
        state = self.get_deployment_state(deployment_id)
        previous_status = state.status if state else None
        now = time.time()
        
        if state is None:
            state = DeploymentState(
                deployment_id=deployment_id,
                namespace=namespace,
                app_name=deployment_name,
                version=version,
                status=DeploymentStatus.IN_PROGRESS,
                previous_version=None,
                retry_count=0,
                failure_type=None,
                timestamp=now,
                metadata={},
                started_at=now
            )
        else:
            state.status = DeploymentStatus.IN_PROGRESS
            state.version = version
            state.finished_at = None
            if state.started_at is None:
                state.started_at = now
        
        if metadata:
            state.metadata.update(metadata)
        
        self.save_deployment_state(state)
        return StateTransition(state, previous_status)
    
    def record_deployment_succeeded(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        metadata: Optional[Dict] = None
    ) -> StateTransition:
        """Persist a success event and remember the version as a rollback target"""
        state = self.get_deployment_state(deployment_id)
        previous_status = state.status if state else None
        
        if state is None:
            state = DeploymentState(
                deployment_id=deployment_id,
                namespace=namespace,
                app_name=deployment_name,
                version=version,
                status=DeploymentStatus.SUCCESS,
                previous_version=None,
                retry_count=0,
                failure_type=None,
                timestamp=time.time(),
                metadata={}
            )
        
        state.status = DeploymentStatus.SUCCESS
        if previous_status is not DeploymentStatus.SUCCESS:
            state.finished_at = None
        
        if metadata:
            state.metadata.update(metadata)
        
        self.save_deployment_state(state)
        self.save_successful_deployment_version(
            namespace=namespace,
            deployment_name=deployment_name,
            version=version
        )
        return StateTransition(state, previous_status)
    
    def get_deployment_state(self, deployment_id: str) -> Optional[DeploymentState]:
        """
        Retrieve deployment state from Redis with proper Enum deserialization        
        """
        key = f"deployment:{deployment_id}"
        
        try:
            data = self.redis_client.get(key)
            if data:
                known_fields = {f.name for f in fields(DeploymentState)}
                state_dict = {
                    key: value for key, value in json.loads(data).items()
                    if key in known_fields
                }
                
                # Convert string values back to Enums
                state_dict['status'] = DeploymentStatus(state_dict['status'])
                if state_dict.get('failure_type'):
                    state_dict['failure_type'] = FailureType(state_dict['failure_type'])
                
                audit_logger.log_event(
                    action=AuditAction.STATE_RETRIEVED,
                    deployment_id=deployment_id,
                    success=True
                )
                
                return DeploymentState(**state_dict)
            
            return None
            
        except Exception as e:
            logger.error(
                f"Failed to retrieve deployment state for {deployment_id}: {e}",
                exc_info=True
            )
            return None
    
    def check_deployment_health(
        self,
        namespace: str,
        deployment_name: str,
        timeout: int = 300,
        deployment_id: Optional[str] = None,
        expected_generation: Optional[int] = None
    ) -> bool:
        """Run the health check and record its duration and result"""
        started = time.perf_counter()
        healthy = False
        try:
            healthy = self._wait_for_healthy_deployment(
                namespace,
                deployment_name,
                timeout,
                deployment_id,
                expected_generation
            )
            return healthy
        finally:
            metrics.record_health_check(
                namespace,
                deployment_name,
                healthy,
                time.perf_counter() - started
            )
    
    def _wait_for_healthy_deployment(
        self,
        namespace: str,
        deployment_name: str,
        timeout: int,
        deployment_id: Optional[str],
        expected_generation: Optional[int]
    ) -> bool:
        """
        Check if deployment is healthy (pods + application health)
        Properly handles application health check failures
        
        With expected_generation, pods only count once that generation has
        finished rolling out, so replicas of the old revision cannot pass.
        
        Returns True if all checks pass
        """
        start_time = time.time()
        app_health_failures = 0  # Track consecutive app health failures
        
        # Log health check start
        if deployment_id:
            audit_logger.log_health_check_started(
                deployment_id=deployment_id,
                namespace=namespace,
                deployment_name=deployment_name,
                timeout_seconds=timeout
            )
        
        while time.time() - start_time < timeout:
            try:
                deployment = self.k8s_apps.read_namespaced_deployment(
                    name=deployment_name,
                    namespace=namespace
                )
                
                replicas = deployment.spec.replicas
                ready_replicas = deployment.status.ready_replicas or 0
                
                rolled_out = (
                    expected_generation is None
                    or rollout_complete(deployment, expected_generation)
                )
                
                # Check if all pods are ready
                if rolled_out and ready_replicas == replicas:
                    # Additionally check application health endpoint
                    if self.check_application_health(namespace, deployment_name):
                        duration = time.time() - start_time
                        
                        logger.info(
                            f"Deployment {deployment_name} is fully healthy: "
                            f"{ready_replicas}/{replicas} pods ready, application responding"
                        )
                        
                        if deployment_id:
                            audit_logger.log_health_check_passed(
                                deployment_id=deployment_id,
                                namespace=namespace,
                                deployment_name=deployment_name,
                                duration_seconds=duration,
                                ready_replicas=ready_replicas,
                                desired_replicas=replicas
                            )
                        
                        return True
                    else:
                        app_health_failures += 1
                        logger.warning(
                            f"Pods ready but application health check failed "
                            f"({app_health_failures}/{self.max_app_health_failures})"
                        )
                        
                        # If too many consecutive failures, give up
                        if app_health_failures >= self.max_app_health_failures:
                            logger.error(
                                f"Application health check failed "
                                f"{app_health_failures} times - marking as unhealthy"
                            )
                            
                            if deployment_id:
                                audit_logger.log_health_check_failed(
                                    deployment_id=deployment_id,
                                    namespace=namespace,
                                    deployment_name=deployment_name,
                                    reason=f"Application health check failed {app_health_failures} times",
                                    ready_replicas=ready_replicas,
                                    desired_replicas=replicas
                                )
                            
                            return False
                else:
                    # Reset failure counter if pods aren't ready yet
                    app_health_failures = 0
                
                logger.info(
                    f"Waiting for deployment {deployment_name}: "
                    f"{ready_replicas}/{replicas} pods ready"
                )
                time.sleep(10)
                
            except ApiException as e:
                logger.error(f"Error checking deployment health: {e}")
                
                if deployment_id:
                    audit_logger.log_health_check_failed(
                        deployment_id=deployment_id,
                        namespace=namespace,
                        deployment_name=deployment_name,
                        reason=f"API error: {str(e)}",
                        ready_replicas=0,
                        desired_replicas=0
                    )
                
                return False
        
        # Timeout reached
        logger.error(f"Deployment {deployment_name} health check timeout")
        
        if deployment_id:
            try:
                deployment = self.k8s_apps.read_namespaced_deployment(
                    name=deployment_name,
                    namespace=namespace
                )
                ready_replicas = deployment.status.ready_replicas or 0
                replicas = deployment.spec.replicas
            except:
                ready_replicas = 0
                replicas = 0
            
            audit_logger.log_health_check_failed(
                deployment_id=deployment_id,
                namespace=namespace,
                deployment_name=deployment_name,
                reason="Health check timeout",
                ready_replicas=ready_replicas,
                desired_replicas=replicas
            )
        
        return False
    
    def get_live_version(
        self,
        namespace: str,
        deployment_name: str
    ) -> Optional[str]:
        """
        Read the image tag the Deployment is running now

        Returns None when the image is pinned by digest or has no tag.
        Raises ApiException or ContainerNotFoundError when it cannot be read.
        """
        deployment = self.k8s_apps.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )
        container = find_container(deployment, deployment_name)
        reference = split_image_reference(container.image)
        return reference.tag if reference.digest is None else None
    
    def save_successful_deployment_version(
        self,
        namespace: str,
        deployment_name: str,
        version: str
    ):
        """
        Store successful deployment version for future rollbacks
        Called when deployment succeeds
        """
        redis_key = f"last_successful:{namespace}:{deployment_name}"
        
        try:
            self.redis_client.set(redis_key, version)
            
            logger.info(
                f"Saved successful version {version} for {namespace}/{deployment_name}"
            )
            
            audit_logger.log_event(
                action=AuditAction.STATE_SAVED,
                namespace=namespace,
                deployment_name=deployment_name,
                version=version,
                success=True,
                details={"type": "successful_version"}
            )
            
        except Exception as e:
            logger.error(
                f"Failed to save successful version: {e}",
                exc_info=True
            )
    
    def get_previous_version(
        self,
        namespace: str,
        deployment_name: str
    ) -> Optional[str]:
        """
        Get previous successful deployment version with multiple fallback strategies
        
        FIXED: More robust version tracking with Redis caching
        """
        # Strategy 1: Check Redis for last successful deployment
        redis_key = f"last_successful:{namespace}:{deployment_name}"
        
        try:
            cached_version = self.redis_client.get(redis_key)
            if cached_version:
                logger.info(
                    f"Found previous version in Redis: {cached_version}"
                )
                return cached_version
        except Exception as e:
            logger.warning(f"Error checking Redis for previous version: {e}")
        
        # Strategy 2: Check ReplicaSets with validation
        try:
            replicasets = self.k8s_apps.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=f"app={deployment_name}"
            )
            
            # Filter for ReplicaSets with version labels
            versioned_rs = [
                rs for rs in replicasets.items
                if rs.metadata.labels and 'version' in rs.metadata.labels
            ]
            
            if not versioned_rs:
                logger.warning(
                    f"No versioned ReplicaSets found for {deployment_name}"
                )
                return None
            
            # Sort by creation time
            sorted_rs = sorted(
                versioned_rs,
                key=lambda x: x.metadata.creation_timestamp,
                reverse=True
            )
            
            # Return second most recent version (if available)
            if len(sorted_rs) >= 2:
                previous_version = sorted_rs[1].metadata.labels.get('version')
                logger.info(
                    f"Found previous version from ReplicaSets: {previous_version}"
                )
                return previous_version
            
            # If only one ReplicaSet exists, can't rollback
            logger.warning(
                f"Only one ReplicaSet found for {deployment_name} - "
                f"cannot determine previous version"
            )
            return None
            
        except ApiException as e:
            logger.error(
                f"Error getting previous version from Kubernetes: {e}"
            )
            return None
    
    def rollback_deployment(
        self,
        namespace: str,
        deployment_name: str,
        target_version: str,
        deployment_id: Optional[str] = None,
        reason: str = "retries exhausted"
    ) -> bool:
        """
        Rollback deployment to a previous version with full audit trail

        Patches only the container image and version labels, then waits for
        the new revision to finish rolling out and report healthy.
        """
        start_time = time.time()
        current_version = "unknown"
        
        try:
            deployment = self.k8s_apps.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace
            )
            container = find_container(deployment, deployment_name)
            current_reference = split_image_reference(container.image)
            current_version = current_reference.tag or "unknown"
            target_image = with_tag(container.image, target_version)
            
            logger.info(
                f"Rolling back {deployment_name} from {current_version} "
                f"to version {target_version}"
            )
            
            if deployment_id:
                audit_logger.log_rollback_initiated(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    current_version=current_version,
                    target_version=target_version,
                    reason=reason
                )
            
            patched = self.k8s_apps.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body={
                    "metadata": {"labels": {"version": target_version}},
                    "spec": {"template": {
                        "metadata": {"labels": {"version": target_version}},
                        "spec": {"containers": [
                            {"name": container.name, "image": target_image}
                        ]}
                    }}
                }
            )
            
            patched_image = find_container(patched, deployment_name).image
            if patched_image != target_image:
                raise RollbackError(
                    f"Deployment runs {patched_image} after the patch, "
                    f"expected {target_image}"
                )
            
            logger.info(f"Rollback initiated for {deployment_name}")
            
            # Wait for rollback to complete
            if self.check_deployment_health(
                namespace,
                deployment_name,
                deployment_id=deployment_id,
                expected_generation=patched.metadata.generation
            ):
                duration = time.time() - start_time
                
                logger.info(
                    f"Rollback successful for {deployment_name} "
                    f"({duration:.2f}s)"
                )
                
                if deployment_id:
                    audit_logger.log_rollback_completed(
                        deployment_id=deployment_id,
                        namespace=namespace,
                        deployment_name=deployment_name,
                        target_version=target_version,
                        duration_seconds=duration
                    )
                
                return True
            else:
                logger.error(f"Rollback failed for {deployment_name}")
                
                if deployment_id:
                    audit_logger.log_rollback_failed(
                        deployment_id=deployment_id,
                        namespace=namespace,
                        deployment_name=deployment_name,
                        target_version=target_version,
                        error="Health check failed after rollback"
                    )
                
                return False
                
        except Exception as e:
            logger.exception(
                f"Error during rollback of {deployment_name} from {current_version}"
            )
            
            if deployment_id:
                audit_logger.log_rollback_failed(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    target_version=target_version,
                    error=f"{type(e).__name__}: {e}"
                )
            
            return False
    
    async def trigger_github_workflow_rerun(
        self,
        workflow_run_id: str,
        backoff_seconds: float
    ) -> bool:
        """
        Trigger GitHub Actions workflow re-run after backoff delay
            
        Args:
            workflow_run_id: GitHub Actions workflow run ID
            backoff_seconds: Delay before retry
            
        Returns:
            True if re-run triggered successfully
        """
        if not self.github_token or not self.github_repo:
            logger.error(
                "Cannot trigger GitHub workflow re-run: "
                "GITHUB_TOKEN or GITHUB_REPO not configured"
            )
            return False
        
        # Wait for backoff period
        logger.info(f"⏳ Waiting {backoff_seconds}s before retry...")
        await asyncio.sleep(backoff_seconds)
        
        # Trigger re-run via GitHub API
        url = (
            f"https://api.github.com/repos/{self.github_repo}/"
            f"actions/runs/{workflow_run_id}/rerun"
        )
        
        headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        try:
            response = requests.post(url, headers=headers, timeout=10)
            
            if response.status_code == 201:
                logger.info(
                    f"Successfully triggered workflow re-run: {workflow_run_id}"
                )
                return True
            elif response.status_code == 403:
                logger.error(
                    f"GitHub API permission denied. "
                    f"Ensure GITHUB_TOKEN has 'actions:write' permission."
                )
                return False
            else:
                logger.error(
                    f"Failed to trigger re-run: "
                    f"{response.status_code} - {response.text}"
                )
                return False
                
        except requests.exceptions.Timeout:
            logger.error("GitHub API request timeout")
            return False
        except Exception as e:
            logger.error(f"Error triggering GitHub workflow re-run: {e}")
            return False
    
    async def handle_deployment_failure(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        failure_type: FailureType,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Main failure handling logic with retry execution

        Args:
            metadata: Webhook metadata (e.g. workflow_run_id) merged into the
                deployment state before the retry decision is made
        """
        decision_started = time.perf_counter()
        
        # Log failure detection
        audit_logger.log_failure_detected(
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=version,
            failure_type=failure_type.value,
            retry_count=0
        )
        
        # Get or create deployment state
        state = self.get_deployment_state(deployment_id)
        if not state:
            state = DeploymentState(
                deployment_id=deployment_id,
                namespace=namespace,
                app_name=deployment_name,
                version=version,
                status=DeploymentStatus.FAILED,
                previous_version=None,
                retry_count=0,
                failure_type=failure_type,
                timestamp=time.time(),
                metadata={}
            )
        
        if state.previous_version is None:
            state.previous_version = await asyncio.to_thread(
                self.get_previous_version, namespace, deployment_name
            )
        
        if metadata:
            state.metadata.update(metadata)
        
        if state.first_failure_at is None:
            state.first_failure_at = time.time()
        
        state.retry_count += 1
        state.failure_type = failure_type
        
        # Decision logic with exponential backoff
        if state.retry_count <= self.max_retries:
            # Calculate backoff delay
            backoff_delay = self.calculate_backoff(state.retry_count)
            
            # Get workflow run ID from metadata
            workflow_run_id = state.metadata.get('workflow_run_id')
            if not workflow_run_id:
                logger.error(
                    "No workflow_run_id in metadata - cannot trigger retry. "
                    "Ensure GitHub Actions webhook includes workflow_run_id."
                )
                
                self._observe_decision(decision_started)
                
                # Fall back to alert
                state.status = DeploymentStatus.FAILED
                self.save_deployment_state(state)
                
                audit_logger.log_manual_intervention_required(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    version=version,
                    reason="Cannot retry - missing workflow_run_id in metadata"
                )
                
                return {
                    'action': 'alert',
                    'message': 'Cannot retry - missing workflow_run_id. Manual intervention required.'
                }
            
            # Retry deployment
            logger.info(
                f"Retrying deployment (attempt {state.retry_count}/{self.max_retries}) "
                f"with {backoff_delay}s backoff"
            )
            
            audit_logger.log_retry_initiated(
                deployment_id=deployment_id,
                namespace=namespace,
                deployment_name=deployment_name,
                version=version,
                retry_count=state.retry_count,
                backoff_seconds=backoff_delay
            )
            
            self._observe_decision(decision_started)
            
            # Schedule and trigger the retry
            state.status = DeploymentStatus.RETRY_SCHEDULED
            self.save_deployment_state(state)
            
            # Schedule retry in background (async)
            # This will be picked up by FastAPI's event loop
            asyncio.create_task(
                self.trigger_github_workflow_rerun(
                    workflow_run_id,
                    backoff_delay
                )
            )
            
            return {
                'action': 'retry',
                'retry_count': state.retry_count,
                'backoff_seconds': backoff_delay,
                'workflow_run_id': workflow_run_id,
                'message': (
                    f'Retry {state.retry_count}/{self.max_retries} scheduled '
                    f'in {backoff_delay}s'
                )
            }
        
        elif failure_type in NON_ROLLBACK_FAILURES:
            reason = (
                f"{failure_type.value} happened before anything was deployed, "
                f"so there is nothing to roll back"
            )
            self._observe_decision(decision_started)
            return self._alert(state, version, reason)
        
        elif state.previous_version:
            blocked = await self._rollback_blocker(state, version)
            self._observe_decision(decision_started)
            if blocked:
                return blocked
            
            # Rollback to previous version
            logger.warning(
                f"Max retries exceeded ({state.retry_count}), "
                f"initiating rollback to {state.previous_version}"
            )
            
            state.status = DeploymentStatus.ROLLING_BACK
            self.save_deployment_state(state)
            
            success = await asyncio.to_thread(
                self.rollback_deployment,
                namespace,
                deployment_name,
                state.previous_version,
                deployment_id=deployment_id
            )
            
            if success:
                state.status = DeploymentStatus.ROLLED_BACK
                self.save_deployment_state(state)
                return {
                    'action': 'rollback',
                    'previous_version': state.previous_version,
                    'message': f'Rolled back to version {state.previous_version}'
                }
            else:
                state.status = DeploymentStatus.FAILED
                self.save_deployment_state(state)
                
                audit_logger.log_manual_intervention_required(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    version=version,
                    reason="Rollback failed after exceeding retry threshold"
                )
                
                return {
                    'action': 'alert',
                    'message': 'Rollback failed - manual intervention required'
                }
        
        else:
            # No previous version or other issues - alert
            logger.critical(
                f"Cannot auto-recover deployment {deployment_id} - "
                f"no previous version available"
            )
            self._observe_decision(decision_started)
            state.status = DeploymentStatus.FAILED
            self.save_deployment_state(state)
            
            audit_logger.log_manual_intervention_required(
                deployment_id=deployment_id,
                namespace=namespace,
                deployment_name=deployment_name,
                version=version,
                reason="No previous version available for rollback"
            )
            
            return {
                'action': 'alert',
                'message': 'No previous version available - manual intervention required'
            }
    
    @staticmethod
    def _observe_decision(started: float) -> None:
        metrics.rollback_decision_duration_seconds.observe(time.perf_counter() - started)
    
    def _alert(self, state: DeploymentState, version: str, reason: str) -> Dict:
        """Mark the deployment failed and ask for manual intervention"""
        state.status = DeploymentStatus.FAILED
        self.save_deployment_state(state)
        
        audit_logger.log_manual_intervention_required(
            deployment_id=state.deployment_id,
            namespace=state.namespace,
            deployment_name=state.app_name,
            version=version,
            reason=reason
        )
        
        return {
            'action': 'alert',
            'message': f'{reason} - manual intervention required'
        }
    
    async def _rollback_blocker(
        self,
        state: DeploymentState,
        version: str
    ) -> Optional[Dict]:
        """
        Check that the failed version is the one running before rolling back

        Returns the response to send instead of rolling back, or None when the
        rollback may go ahead.
        """
        try:
            live_version = await asyncio.to_thread(
                self.get_live_version,
                state.namespace,
                state.app_name
            )
        except ApiException as e:
            return self._alert(
                state, version,
                f"Cannot read the live version (Kubernetes API error {e.status})"
            )
        except ContainerNotFoundError as e:
            return self._alert(state, version, f"Cannot read the live version: {e}")
        
        if live_version is None:
            return self._alert(
                state, version,
                "Live image is not addressed by tag, so it cannot be matched "
                "to the failed version"
            )
        
        if live_version != version:
            logger.warning(
                f"Skipping rollback: live version {live_version} is not the "
                f"failed version {version}"
            )
            state.status = DeploymentStatus.FAILED
            self.save_deployment_state(state)
            audit_logger.log_rollback_skipped(
                deployment_id=state.deployment_id,
                namespace=state.namespace,
                deployment_name=state.app_name,
                failed_version=version,
                live_version=live_version
            )
            return {
                'action': 'skip',
                'live_version': live_version,
                'message': (
                    f'Live version {live_version} is not the failed version '
                    f'{version}; rollback skipped'
                )
            }
        
        return None
    
    def get_deployment_metrics(
        self,
        namespace: str,
        deployment_name: str
    ) -> Dict:
        """Get deployment metrics for monitoring"""
        try:
            deployment = self.k8s_apps.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace
            )
            
            return {
                'replicas': deployment.spec.replicas,
                'ready_replicas': deployment.status.ready_replicas or 0,
                'available_replicas': deployment.status.available_replicas or 0,
                'unavailable_replicas': deployment.status.unavailable_replicas or 0,
                'conditions': [
                    {
                        'type': cond.type,
                        'status': cond.status,
                        'reason': cond.reason
                    }
                    for cond in (deployment.status.conditions or [])
                ]
            }
        except ApiException as e:
            logger.error(f"Error getting deployment metrics: {e}")
            return {}
    
    def get_recent_deployments(
        self,
        namespace: Optional[str] = None,
        limit: int = 10
    ) -> List[DeploymentState]:
        """
        Get recent deployment states for monitoring
        
        NEW: Enables deployment history queries
        """
        try:
            keys = self.redis_client.keys("deployment:*")
            states = []
            
            for key in keys:
                try:
                    deployment_id = key.split(':')[1]
                    state = self.get_deployment_state(deployment_id)
                    if state:
                        # Filter by namespace if specified
                        if namespace is None or state.namespace == namespace:
                            states.append(state)
                except Exception as e:
                    logger.warning(f"Error retrieving state {key}: {e}")
            
            # Sort by timestamp, most recent first
            states.sort(key=lambda x: x.timestamp, reverse=True)
            
            return states[:limit]
            
        except Exception as e:
            logger.error(f"Error getting recent deployments: {e}")
            return []
    
    def cleanup_old_states(self, days_to_keep: int = 7):
        """
        Remove deployment states older than specified days
        
        NEW: Prevents Redis memory leak
        """
        cutoff_time = time.time() - (days_to_keep * 86400)
        
        try:
            # Get all deployment keys
            keys = self.redis_client.keys("deployment:*")
            
            cleaned_count = 0
            for key in keys:
                try:
                    data = self.redis_client.get(key)
                    if data:
                        state_dict = json.loads(data)
                        if state_dict.get('timestamp', 0) < cutoff_time:
                            self.redis_client.delete(key)
                            cleaned_count += 1
                except Exception as e:
                    logger.warning(f"Error cleaning state {key}: {e}")
            
            logger.info(f"🧹 Cleaned up {cleaned_count} old deployment states")
            
        except Exception as e:
            logger.error(f"Error during state cleanup: {e}")