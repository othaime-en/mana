import logging
import time
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import redis
import json
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from src.utils import get_audit_logger, AuditAction


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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


class FailureType(Enum):
    """Types of failures"""
    BUILD_FAILURE = "build_failure"
    TEST_FAILURE = "test_failure"
    DEPLOYMENT_FAILURE = "deployment_failure"
    HEALTH_CHECK_FAILURE = "health_check_failure"
    TIMEOUT = "timeout"


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


class SelfHealingOrchestrator:
    """
    Self-healing orchestrator for CI/CD pipeline
    Monitors deployments and automatically handles failures
    """
    
    def __init__(
        self,
        redis_host: str = 'localhost',
        redis_port: int = 6379,
        max_retries: int = 3,
        rollback_threshold: int = 2,
        initial_backoff: float = 10.0,
        max_backoff: float = 300.0,
        backoff_multiplier: float = 2.0,
        health_check_timeout: int = 5
    ):
        """
        Initialize orchestrator
        
        Args:
            redis_host: Redis host
            redis_port: Redis port
            max_retries: Maximum retry attempts
            rollback_threshold: Failures before rollback
            initial_backoff: Initial retry delay in seconds
            max_backoff: Maximum retry delay in seconds
            backoff_multiplier: Exponential backoff multiplier
            health_check_timeout: HTTP health check timeout in seconds
        """
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True
        )
        self.max_retries = max_retries
        self.rollback_threshold = rollback_threshold
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_multiplier = backoff_multiplier
        self.health_check_timeout = health_check_timeout
        
        # Load Kubernetes config
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
        
        logger.info(
            "Self-Healing Orchestrator initialized",
            extra={
                "max_retries": max_retries,
                "rollback_threshold": rollback_threshold,
                "initial_backoff": initial_backoff,
                "max_backoff": max_backoff
            }
        )
    
    def calculate_backoff(self, retry_count: int) -> float:
        """
        Calculate exponential backoff delay
        
        Args:
            retry_count: Current retry attempt number
            
        Returns:
            Backoff delay in seconds
        """
        backoff = self.initial_backoff * (self.backoff_multiplier ** (retry_count - 1))
        return min(backoff, self.max_backoff)
    
    def check_application_health(
        self,
        namespace: str,
        deployment_name: str,
        port: int = 5000,
        health_path: str = "/health"
    ) -> bool:
        """
        Check application health by calling its health endpoint
        
        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of deployment
            port: Application port
            health_path: Health check endpoint path
            
        Returns:
            True if application is healthy, False otherwise
        """
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
            for pod in pods.items:
                if pod.status.phase != "Running":
                    continue
                
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
                        data = response.json()
                        if data.get('status') == 'healthy':
                            healthy_pods += 1
                            logger.debug(f"Pod {pod.metadata.name} health check passed")
                        else:
                            logger.warning(
                                f"Pod {pod.metadata.name} returned unhealthy status: {data}"
                            )
                    else:
                        logger.warning(
                            f"Pod {pod.metadata.name} health check failed with status {response.status_code}"
                        )
                
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Failed to reach pod {pod.metadata.name}: {e}")
                    continue
            
            # Consider deployment healthy if at least one pod is healthy
            return healthy_pods > 0
            
        except Exception as e:
            logger.error(f"Error checking application health: {e}", exc_info=True)
            return False
    
    def save_deployment_state(self, state: DeploymentState) -> None:
        """Save deployment state to Redis"""
        key = f"deployment:{state.deployment_id}"
        self.redis_client.setex(
            key,
            86400,  # 24 hours TTL
            json.dumps(asdict(state), default=str)
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
        
        logger.info(f"Saved deployment state: {state.deployment_id}")
    
    def get_deployment_state(self, deployment_id: str) -> Optional[DeploymentState]:
        """Retrieve deployment state from Redis"""
        key = f"deployment:{deployment_id}"
        data = self.redis_client.get(key)
        if data:
            state_dict = json.loads(data)
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
    
    def check_deployment_health(
        self,
        namespace: str,
        deployment_name: str,
        timeout: int = 300,
        deployment_id: Optional[str] = None
    ) -> bool:
        """
        Check if deployment is healthy
        
        Enhanced to include:
        - Pod status check
        - Application health endpoint check
        - Audit logging
        
        Returns True if all checks pass
        """
        start_time = time.time()
        
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
                
                # Check if all pods are ready
                if ready_replicas == replicas:
                    # Additionally check application health endpoint
                    if self.check_application_health(namespace, deployment_name):
                        duration = time.time() - start_time
                        
                        logger.info(
                            f"Deployment {deployment_name} is fully healthy: "
                            f"{ready_replicas}/{replicas} ready, application responding"
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
                        logger.info(
                            f"Pods ready but application health check failed for {deployment_name}"
                        )
                
                logger.info(
                    f"Waiting for deployment {deployment_name}: "
                    f"{ready_replicas}/{replicas} ready"
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
    
    def get_previous_version(
        self,
        namespace: str,
        deployment_name: str
    ) -> Optional[str]:
        """Get previous successful deployment version"""
        try:
            # Check ReplicaSets to find previous version
            replicasets = self.k8s_apps.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=f"app={deployment_name}"
            )
            
            # Sort by creation timestamp
            sorted_rs = sorted(
                replicasets.items,
                key=lambda x: x.metadata.creation_timestamp,
                reverse=True
            )
            
            # Return second most recent (previous version)
            if len(sorted_rs) >= 2:
                version = sorted_rs[1].metadata.labels.get('version')
                logger.info(f"Found previous version: {version}")
                return version
            
        except ApiException as e:
            logger.error(f"Error getting previous version: {e}")
        
        return None
    
    def rollback_deployment(
        self,
        namespace: str,
        deployment_name: str,
        target_version: str,
        deployment_id: Optional[str] = None
    ) -> bool:
        """
        Rollback deployment to previous version
        
        Enhanced with:
        - Detailed audit logging
        - Timing metrics
        - Health verification after rollback
        """
        start_time = time.time()
        
        try:
            logger.info(f"Rolling back {deployment_name} to version {target_version}")
            
            if deployment_id:
                audit_logger.log_rollback_initiated(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    current_version="unknown",  # Current version already failed
                    target_version=target_version,
                    reason="Exceeded retry threshold"
                )
            
            # Read current deployment
            deployment = self.k8s_apps.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace
            )
            
            # Update image tag to target version
            for container in deployment.spec.template.spec.containers:
                if container.name == deployment_name:
                    image_parts = container.image.split(':')
                    container.image = f"{image_parts[0]}:{target_version}"
            
            # Update version label
            deployment.metadata.labels['version'] = target_version
            deployment.spec.template.metadata.labels['version'] = target_version
            
            # Apply changes
            self.k8s_apps.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=deployment
            )
            
            logger.info(f"Rollback initiated for {deployment_name}")
            
            # Wait for rollback to complete
            if self.check_deployment_health(
                namespace,
                deployment_name,
                deployment_id=deployment_id
            ):
                duration = time.time() - start_time
                
                logger.info(f"Rollback successful for {deployment_name}")
                
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
                
        except ApiException as e:
            logger.error(f"Error during rollback: {e}")
            
            if deployment_id:
                audit_logger.log_rollback_failed(
                    deployment_id=deployment_id,
                    namespace=namespace,
                    deployment_name=deployment_name,
                    target_version=target_version,
                    error=str(e)
                )
            
            return False
    
    def handle_deployment_failure(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        failure_type: FailureType
    ) -> Dict:
        """
        Main failure handling logic with enhanced features:
        - Exponential backoff for retries
        - Comprehensive audit logging
        - Application health verification
        
        Decides whether to retry, rollback, or alert
        """
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
            previous_version = self.get_previous_version(namespace, deployment_name)
            state = DeploymentState(
                deployment_id=deployment_id,
                namespace=namespace,
                app_name=deployment_name,
                version=version,
                status=DeploymentStatus.FAILED,
                previous_version=previous_version,
                retry_count=0,
                failure_type=failure_type,
                timestamp=time.time(),
                metadata={}
            )
        
        state.retry_count += 1
        state.failure_type = failure_type
        
        # Decision logic with exponential backoff
        if state.retry_count <= self.max_retries:
            # Calculate backoff delay
            backoff_delay = self.calculate_backoff(state.retry_count)
            
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
            
            state.status = DeploymentStatus.IN_PROGRESS
            self.save_deployment_state(state)
            
            return {
                'action': 'retry',
                'retry_count': state.retry_count,
                'backoff_seconds': backoff_delay,
                'message': f'Retrying deployment (attempt {state.retry_count}) after {backoff_delay}s'
            }
        
        elif state.retry_count > self.rollback_threshold and state.previous_version:
            # Rollback to previous version
            logger.warning(
                f"Max retries exceeded, initiating rollback to {state.previous_version}"
            )
            
            state.status = DeploymentStatus.ROLLING_BACK
            self.save_deployment_state(state)
            
            success = self.rollback_deployment(
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
                    reason="Rollback failed"
                )
                
                return {
                    'action': 'alert',
                    'message': 'Rollback failed - manual intervention required'
                }
        
        else:
            # No previous version or other issues - alert
            logger.critical(f"Cannot auto-recover deployment {deployment_id}")
            state.status = DeploymentStatus.FAILED
            self.save_deployment_state(state)
            
            audit_logger.log_manual_intervention_required(
                deployment_id=deployment_id,
                namespace=namespace,
                deployment_name=deployment_name,
                version=version,
                reason="No previous version available or auto-recovery exhausted"
            )
            
            return {
                'action': 'alert',
                'message': 'Auto-recovery failed - manual intervention required'
            }
    
    def get_deployment_metrics(self, namespace: str, deployment_name: str) -> Dict:
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