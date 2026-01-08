# orchestrator/src/orchestrator.py
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import redis
import json
from kubernetes import client, config
from kubernetes.client.rest import ApiException


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
        rollback_threshold: int = 2
    ):
        """Initialize orchestrator"""
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True
        )
        self.max_retries = max_retries
        self.rollback_threshold = rollback_threshold
        
        # Load Kubernetes config
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
        
        logger.info("Self-Healing Orchestrator initialized")
    
    def save_deployment_state(self, state: DeploymentState) -> None:
        """Save deployment state to Redis"""
        key = f"deployment:{state.deployment_id}"
        self.redis_client.setex(
            key,
            86400,  # 24 hours TTL
            json.dumps(asdict(state), default=str)
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
            return DeploymentState(**state_dict)
        return None
    
    def check_deployment_health(
        self,
        namespace: str,
        deployment_name: str,
        timeout: int = 300
    ) -> bool:
        """
        Check if deployment is healthy
        Returns True if all pods are ready
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                deployment = self.k8s_apps.read_namespaced_deployment(
                    name=deployment_name,
                    namespace=namespace
                )
                
                replicas = deployment.spec.replicas
                ready_replicas = deployment.status.ready_replicas or 0
                
                if ready_replicas == replicas:
                    logger.info(f"Deployment {deployment_name} is healthy: {ready_replicas}/{replicas} ready")
                    return True
                
                logger.info(f"Waiting for deployment {deployment_name}: {ready_replicas}/{replicas} ready")
                time.sleep(10)
                
            except ApiException as e:
                logger.error(f"Error checking deployment health: {e}")
                return False
        
        logger.error(f"Deployment {deployment_name} health check timeout")
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
        target_version: str
    ) -> bool:
        """
        Rollback deployment to previous version
        """
        try:
            logger.info(f"Rolling back {deployment_name} to version {target_version}")
            
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
            if self.check_deployment_health(namespace, deployment_name):
                logger.info(f"Rollback successful for {deployment_name}")
                return True
            else:
                logger.error(f"Rollback failed for {deployment_name}")
                return False
                
        except ApiException as e:
            logger.error(f"Error during rollback: {e}")
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
        Main failure handling logic
        Decides whether to retry, rollback, or alert
        """
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
        
        # Decision logic
        if state.retry_count <= self.max_retries:
            # Retry deployment
            logger.info(f"Retrying deployment (attempt {state.retry_count}/{self.max_retries})")
            state.status = DeploymentStatus.IN_PROGRESS
            self.save_deployment_state(state)
            
            return {
                'action': 'retry',
                'retry_count': state.retry_count,
                'message': f'Retrying deployment (attempt {state.retry_count})'
            }
        
        elif state.retry_count > self.rollback_threshold and state.previous_version:
            # Rollback to previous version
            logger.warning(f"Max retries exceeded, initiating rollback to {state.previous_version}")
            state.status = DeploymentStatus.ROLLING_BACK
            self.save_deployment_state(state)
            
            success = self.rollback_deployment(
                namespace,
                deployment_name,
                state.previous_version
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
                return {
                    'action': 'alert',
                    'message': 'Rollback failed - manual intervention required'
                }
        
        else:
            # No previous version or other issues - alert
            logger.critical(f"Cannot auto-recover deployment {deployment_id}")
            state.status = DeploymentStatus.FAILED
            self.save_deployment_state(state)
            
            return {
                'action': 'alert',
                'message': 'Auto-recovery failed - manual intervention required'
            }