
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.orchestrator import (
    SelfHealingOrchestrator,
    DeploymentStatus,
    FailureType,
    DeploymentState
)
import time


@pytest.fixture
def orchestrator():
    """Create orchestrator instance with mocked dependencies"""
    with patch('src.orchestrator.redis.Redis') as mock_redis, \
         patch('src.orchestrator.config.load_kube_config'), \
         patch('src.orchestrator.client.AppsV1Api') as mock_apps, \
         patch('src.orchestrator.client.CoreV1Api') as mock_core:
        
        orch = SelfHealingOrchestrator(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2
        )
        orch.redis_client = mock_redis.return_value
        orch.k8s_apps = mock_apps.return_value
        orch.k8s_core = mock_core.return_value
        
        yield orch


def test_save_and_get_deployment_state(orchestrator):
    """Test saving and retrieving deployment state"""
    state = DeploymentState(
        deployment_id='test-123',
        namespace='production',
        app_name='sample-app',
        version='1.0.0',
        status=DeploymentStatus.IN_PROGRESS,
        previous_version='0.9.0',
        retry_count=0,
        failure_type=None,
        timestamp=time.time(),
        metadata={}
    )
    
    orchestrator.redis_client.setex = Mock()
    orchestrator.save_deployment_state(state)
    orchestrator.redis_client.setex.assert_called_once()


def test_check_deployment_health_success(orchestrator):
    """Test successful health check"""
    mock_deployment = Mock()
    mock_deployment.spec.replicas = 3
    mock_deployment.status.ready_replicas = 3
    
    orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
    
    result = orchestrator.check_deployment_health('production', 'sample-app', timeout=10)
    assert result is True


def test_check_deployment_health_failure(orchestrator):
    """Test failed health check"""
    mock_deployment = Mock()
    mock_deployment.spec.replicas = 3
    mock_deployment.status.ready_replicas = 1
    
    orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
    
    result = orchestrator.check_deployment_health('production', 'sample-app', timeout=5)
    assert result is False


def test_rollback_deployment(orchestrator):
    """Test deployment rollback"""
    mock_deployment = Mock()
    mock_deployment.spec.template.spec.containers = [
        Mock(name='sample-app', image='sample-app:1.0.0')
    ]
    mock_deployment.metadata.labels = {'version': '1.0.0'}
    mock_deployment.spec.template.metadata.labels = {'version': '1.0.0'}
    
    orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
    orchestrator.k8s_apps.patch_namespaced_deployment.return_value = mock_deployment
    
    with patch.object(orchestrator, 'check_deployment_health', return_value=True):
        result = orchestrator.rollback_deployment('production', 'sample-app', '0.9.0')
        assert result is True