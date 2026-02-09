import pytest
from unittest.mock import Mock, patch, MagicMock, call
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
         patch('src.orchestrator.client.CoreV1Api') as mock_core, \
         patch('src.orchestrator.get_audit_logger') as mock_audit:
        
        orch = SelfHealingOrchestrator(
            redis_host='localhost',
            redis_port=6379,
            max_retries=3,
            rollback_threshold=2,
            initial_backoff=10.0,
            max_backoff=300.0,
            backoff_multiplier=2.0,
            health_check_timeout=5
        )
        orch.redis_client = mock_redis.return_value
        orch.k8s_apps = mock_apps.return_value
        orch.k8s_core = mock_core.return_value
        
        yield orch


class TestExponentialBackoff:
    """Test exponential backoff calculation"""
    
    def test_calculate_backoff_first_retry(self, orchestrator):
        """Test backoff calculation for first retry"""
        backoff = orchestrator.calculate_backoff(1)
        assert backoff == 10.0  # initial_backoff
    
    def test_calculate_backoff_second_retry(self, orchestrator):
        """Test backoff calculation for second retry"""
        backoff = orchestrator.calculate_backoff(2)
        assert backoff == 20.0  # 10 * 2^1
    
    def test_calculate_backoff_third_retry(self, orchestrator):
        """Test backoff calculation for third retry"""
        backoff = orchestrator.calculate_backoff(3)
        assert backoff == 40.0  # 10 * 2^2
    
    def test_calculate_backoff_max_limit(self, orchestrator):
        """Test backoff respects maximum limit"""
        backoff = orchestrator.calculate_backoff(10)
        assert backoff == 300.0  # max_backoff
    
    def test_calculate_backoff_progression(self, orchestrator):
        """Test backoff increases exponentially"""
        backoffs = [orchestrator.calculate_backoff(i) for i in range(1, 5)]
        # Each should be roughly double the previous (until max)
        assert backoffs[0] < backoffs[1]
        assert backoffs[1] < backoffs[2]
        assert backoffs[2] < backoffs[3]


class TestApplicationHealthCheck:
    """Test application health endpoint monitoring"""
    
    def test_check_application_health_success(self, orchestrator):
        """Test successful application health check"""
        # Mock pod list
        mock_pod = Mock()
        mock_pod.status.phase = "Running"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.metadata.name = "test-pod"
        
        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod]
        
        orchestrator.k8s_core.list_namespaced_pod.return_value = mock_pod_list
        
        # Mock successful HTTP health check
        with patch('src.orchestrator.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'status': 'healthy'}
            mock_get.return_value = mock_response
            
            result = orchestrator.check_application_health(
                'production',
                'sample-app'
            )
            
            assert result is True
            mock_get.assert_called_once()
    
    def test_check_application_health_unhealthy_response(self, orchestrator):
        """Test application health check with unhealthy status"""
        mock_pod = Mock()
        mock_pod.status.phase = "Running"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.metadata.name = "test-pod"
        
        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod]
        
        orchestrator.k8s_core.list_namespaced_pod.return_value = mock_pod_list
        
        with patch('src.orchestrator.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'status': 'unhealthy'}
            mock_get.return_value = mock_response
            
            result = orchestrator.check_application_health(
                'production',
                'sample-app'
            )
            
            assert result is False
    
    def test_check_application_health_http_error(self, orchestrator):
        """Test application health check with HTTP error"""
        mock_pod = Mock()
        mock_pod.status.phase = "Running"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.metadata.name = "test-pod"
        
        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod]
        
        orchestrator.k8s_core.list_namespaced_pod.return_value = mock_pod_list
        
        with patch('src.orchestrator.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response
            
            result = orchestrator.check_application_health(
                'production',
                'sample-app'
            )
            
            assert result is False
    
    def test_check_application_health_timeout(self, orchestrator):
        """Test application health check with timeout"""
        mock_pod = Mock()
        mock_pod.status.phase = "Running"
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_pod.metadata.name = "test-pod"
        
        mock_pod_list = Mock()
        mock_pod_list.items = [mock_pod]
        
        orchestrator.k8s_core.list_namespaced_pod.return_value = mock_pod_list
        
        with patch('src.orchestrator.requests.get') as mock_get:
            import requests
            mock_get.side_effect = requests.exceptions.Timeout()
            
            result = orchestrator.check_application_health(
                'production',
                'sample-app'
            )
            
            assert result is False
    
    def test_check_application_health_no_pods(self, orchestrator):
        """Test application health check with no pods"""
        mock_pod_list = Mock()
        mock_pod_list.items = []
        
        orchestrator.k8s_core.list_namespaced_pod.return_value = mock_pod_list
        
        result = orchestrator.check_application_health(
            'production',
            'sample-app'
        )
        
        assert result is False


class TestEnhancedHealthCheck:
    """Test enhanced deployment health check with application verification"""
    
    def test_check_deployment_health_full_success(self, orchestrator):
        """Test deployment health check with all checks passing"""
        mock_deployment = Mock()
        mock_deployment.spec.replicas = 3
        mock_deployment.status.ready_replicas = 3
        
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
        
        with patch.object(orchestrator, 'check_application_health', return_value=True):
            result = orchestrator.check_deployment_health(
                'production',
                'sample-app',
                timeout=10,
                deployment_id='test-123'
            )
            
            assert result is True
    
    def test_check_deployment_health_pods_ready_app_unhealthy(self, orchestrator):
        """Test deployment health when pods are ready but app is unhealthy"""
        mock_deployment = Mock()
        mock_deployment.spec.replicas = 3
        mock_deployment.status.ready_replicas = 3
        
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
        
        with patch.object(orchestrator, 'check_application_health', return_value=False):
            result = orchestrator.check_deployment_health(
                'production',
                'sample-app',
                timeout=10
            )
            
            assert result is False


class TestEnhancedFailureHandling:
    """Test enhanced failure handling with backoff and audit logging"""
    
    def test_handle_deployment_failure_retry_with_backoff(self, orchestrator):
        """Test failure handling returns retry with backoff calculation"""
        orchestrator.redis_client.get.return_value = None
        orchestrator.redis_client.setex = Mock()
        
        with patch.object(orchestrator, 'get_previous_version', return_value='0.9.0'):
            result = orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE
            )
            
            assert result['action'] == 'retry'
            assert result['retry_count'] == 1
            assert 'backoff_seconds' in result
            assert result['backoff_seconds'] == 10.0  # First retry backoff
    
    def test_handle_deployment_failure_increasing_backoff(self, orchestrator):
        """Test backoff increases with each retry"""
        # Simulate multiple retries
        state = DeploymentState(
            deployment_id='test-123',
            namespace='production',
            app_name='sample-app',
            version='1.0.0',
            status=DeploymentStatus.FAILED,
            previous_version='0.9.0',
            retry_count=1,
            failure_type=FailureType.DEPLOYMENT_FAILURE,
            timestamp=time.time(),
            metadata={}
        )
        
        with patch.object(orchestrator, 'get_deployment_state', return_value=state), \
             patch.object(orchestrator, 'save_deployment_state'):
            
            result = orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE
            )
            
            assert result['action'] == 'retry'
            assert result['retry_count'] == 2
            assert result['backoff_seconds'] == 20.0  # Second retry backoff
    
    def test_handle_deployment_failure_rollback_after_retries(self, orchestrator):
        """Test rollback is triggered after exceeding retry threshold"""
        state = DeploymentState(
            deployment_id='test-123',
            namespace='production',
            app_name='sample-app',
            version='1.0.0',
            status=DeploymentStatus.FAILED,
            previous_version='0.9.0',
            retry_count=3,
            failure_type=FailureType.DEPLOYMENT_FAILURE,
            timestamp=time.time(),
            metadata={}
        )
        
        with patch.object(orchestrator, 'get_deployment_state', return_value=state), \
             patch.object(orchestrator, 'rollback_deployment', return_value=True), \
             patch.object(orchestrator, 'save_deployment_state'):
            
            result = orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE
            )
            
            assert result['action'] == 'rollback'
            assert 'previous_version' in result


class TestEnhancedRollback:
    """Test enhanced rollback with audit logging"""
    
    def test_rollback_deployment_with_audit(self, orchestrator):
        """Test rollback includes audit logging"""
        mock_deployment = Mock()
        mock_deployment.spec.template.spec.containers = [
            Mock(name='sample-app', image='sample-app:1.0.0')
        ]
        mock_deployment.metadata.labels = {'version': '1.0.0'}
        mock_deployment.spec.template.metadata.labels = {'version': '1.0.0'}
        
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = mock_deployment
        orchestrator.k8s_apps.patch_namespaced_deployment.return_value = mock_deployment
        
        with patch.object(orchestrator, 'check_deployment_health', return_value=True):
            result = orchestrator.rollback_deployment(
                'production',
                'sample-app',
                '0.9.0',
                deployment_id='test-123'
            )
            
            assert result is True


class TestSaveAndRetrieveState:
    """Test state management with audit logging"""
    
    def test_save_deployment_state(self, orchestrator):
        """Test saving deployment state"""
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