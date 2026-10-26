import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock, call
from kubernetes.client.rest import ApiException
from src.orchestrator import (
    SelfHealingOrchestrator,
    DeploymentStatus,
    FailureType,
    DeploymentState,
    ContainerNotFoundError,
    find_container
)
import time


@pytest.fixture
def orchestrator():
    """Create orchestrator instance with mocked dependencies"""
    with patch('src.orchestrator.redis.Redis') as mock_redis, \
         patch('src.orchestrator.k8s_config.load_kube_config'), \
         patch('src.orchestrator.client.AppsV1Api') as mock_apps, \
         patch('src.orchestrator.client.CoreV1Api') as mock_core, \
         patch('src.orchestrator.get_audit_logger') as mock_audit:
        
        # Use use_config=False to avoid loading from environment in tests
        orch = SelfHealingOrchestrator(use_config=False)
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
    
    @pytest.mark.asyncio
    async def test_handle_deployment_failure_retry_with_backoff(self, orchestrator):
        """Test failure handling returns retry with backoff calculation"""
        orchestrator.redis_client.get.return_value = None
        orchestrator.redis_client.setex = Mock()
        
        with patch.object(orchestrator, 'get_previous_version', return_value='0.9.0'), \
             patch.object(orchestrator, 'trigger_github_workflow_rerun', new=AsyncMock()) as rerun:
            result = await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE,
                metadata={'workflow_run_id': '12345'}
            )
            
            assert result['action'] == 'retry'
            assert result['retry_count'] == 1
            assert 'backoff_seconds' in result
            assert result['backoff_seconds'] == 10.0  # First retry backoff
            assert result['workflow_run_id'] == '12345'
            rerun.assert_called_once_with('12345', 10.0)
    
    @pytest.mark.asyncio
    async def test_handle_deployment_failure_increasing_backoff(self, orchestrator):
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
             patch.object(orchestrator, 'save_deployment_state'), \
             patch.object(orchestrator, 'trigger_github_workflow_rerun', new=AsyncMock()) as rerun:
            
            result = await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE,
                metadata={'workflow_run_id': '12345'}
            )
            
            assert result['action'] == 'retry'
            assert result['retry_count'] == 2
            assert result['backoff_seconds'] == 20.0  # Second retry backoff
            rerun.assert_called_once_with('12345', 20.0)
    
    @pytest.mark.asyncio
    async def test_handle_deployment_failure_alerts_without_workflow_run_id(self, orchestrator):
        """Test failure without workflow_run_id in state or webhook falls back to alert"""
        orchestrator.redis_client.get.return_value = None
        orchestrator.redis_client.setex = Mock()
        
        with patch.object(orchestrator, 'get_previous_version', return_value='0.9.0'), \
             patch.object(orchestrator, 'trigger_github_workflow_rerun', new=AsyncMock()) as rerun:
            result = await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE,
                metadata={'commit': 'abc123'}
            )
            
            assert result['action'] == 'alert'
            rerun.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_deployment_failure_persists_webhook_metadata(self, orchestrator):
        """Test webhook metadata is merged into the saved deployment state"""
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
            metadata={'commit': 'abc123'}
        )
        
        with patch.object(orchestrator, 'get_deployment_state', return_value=state), \
             patch.object(orchestrator, 'save_deployment_state') as save, \
             patch.object(orchestrator, 'trigger_github_workflow_rerun', new=AsyncMock()):
            await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE,
                metadata={'workflow_run_id': '12345'}
            )
            
            saved_state = save.call_args[0][0]
            assert saved_state.metadata == {'commit': 'abc123', 'workflow_run_id': '12345'}
    
    @pytest.mark.asyncio
    async def test_handle_deployment_failure_rollback_after_retries(self, orchestrator):
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
             patch.object(orchestrator, 'get_live_version', return_value='1.0.0'), \
             patch.object(orchestrator, 'rollback_deployment', return_value=True), \
             patch.object(orchestrator, 'save_deployment_state'):
            
            result = await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=FailureType.DEPLOYMENT_FAILURE
            )
            
            assert result['action'] == 'rollback'
            assert 'previous_version' in result


def exhausted_state(failure_type=FailureType.DEPLOYMENT_FAILURE, previous_version='0.9.0'):
    return DeploymentState(
        deployment_id='test-123',
        namespace='production',
        app_name='sample-app',
        version='1.0.0',
        status=DeploymentStatus.FAILED,
        previous_version=previous_version,
        retry_count=3,
        failure_type=failure_type,
        timestamp=time.time(),
        metadata={}
    )


class TestRollbackGuards:
    """Rollback only happens for a deployed version that is still live"""

    async def fail(self, orchestrator, failure_type, state, **patches):
        live = patches.pop('live', '1.0.0')
        live_patch = (
            patch.object(orchestrator, 'get_live_version', side_effect=live)
            if isinstance(live, Exception)
            else patch.object(orchestrator, 'get_live_version', return_value=live)
        )
        with patch.object(orchestrator, 'get_deployment_state', return_value=state), \
             live_patch, \
             patch.object(orchestrator, 'rollback_deployment', return_value=True) as rollback, \
             patch.object(orchestrator, 'save_deployment_state') as save:
            result = await orchestrator.handle_deployment_failure(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type=failure_type
            )
        return result, rollback, save

    @pytest.mark.asyncio
    @pytest.mark.parametrize('failure_type', [
        FailureType.BUILD_FAILURE, FailureType.TEST_FAILURE
    ])
    async def test_build_and_test_failures_never_roll_back(self, orchestrator, failure_type):
        result, rollback, save = await self.fail(
            orchestrator, failure_type, exhausted_state(failure_type)
        )

        assert result['action'] == 'alert'
        assert 'nothing to roll back' in result['message']
        rollback.assert_not_called()
        assert save.call_args[0][0].status == DeploymentStatus.FAILED

    @pytest.mark.asyncio
    @pytest.mark.parametrize('failure_type', [
        FailureType.DEPLOYMENT_FAILURE,
        FailureType.HEALTH_CHECK_FAILURE,
        FailureType.SMOKE_TEST_FAILURE,
        FailureType.TIMEOUT,
    ])
    async def test_post_deploy_failures_roll_back_when_failed_version_is_live(
        self, orchestrator, failure_type
    ):
        result, rollback, _ = await self.fail(
            orchestrator, failure_type, exhausted_state(failure_type)
        )

        assert result['action'] == 'rollback'
        rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_is_skipped_when_another_version_is_live(self, orchestrator):
        result, rollback, save = await self.fail(
            orchestrator, FailureType.DEPLOYMENT_FAILURE, exhausted_state(), live='0.9.0'
        )

        assert result['action'] == 'skip'
        assert result['live_version'] == '0.9.0'
        rollback.assert_not_called()
        assert save.call_args[0][0].status == DeploymentStatus.FAILED

    @pytest.mark.asyncio
    async def test_rollback_needs_manual_help_when_live_version_unreadable(self, orchestrator):
        result, rollback, _ = await self.fail(
            orchestrator, FailureType.DEPLOYMENT_FAILURE, exhausted_state(),
            live=ApiException(status=403, reason='Forbidden')
        )

        assert result['action'] == 'alert'
        assert '403' in result['message']
        rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_needs_manual_help_when_container_is_ambiguous(self, orchestrator):
        result, rollback, _ = await self.fail(
            orchestrator, FailureType.DEPLOYMENT_FAILURE, exhausted_state(),
            live=ContainerNotFoundError('no container named sample-app')
        )

        assert result['action'] == 'alert'
        rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_needs_manual_help_when_image_is_pinned_by_digest(self, orchestrator):
        result, rollback, _ = await self.fail(
            orchestrator, FailureType.DEPLOYMENT_FAILURE, exhausted_state(), live=None
        )

        assert result['action'] == 'alert'
        assert 'tag' in result['message']
        rollback.assert_not_called()


def deployment_with(*containers):
    deployment = Mock()
    deployment.spec.template.spec.containers = list(containers)
    return deployment


def container(name, image):
    item = Mock()
    item.name = name
    item.image = image
    return item


class TestRollbackAfterRetries:
    """MAX_RETRIES alone decides when retrying stops and rollback starts"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('max_retries,failures,expected', [
        (1, [1, 2], ['retry', 'rollback']),
        (2, [1, 2, 3], ['retry', 'retry', 'rollback']),
        (3, [1, 2, 3, 4], ['retry', 'retry', 'retry', 'rollback']),
    ])
    async def test_rollback_follows_the_last_retry(
        self, orchestrator, max_retries, failures, expected
    ):
        orchestrator.max_retries = max_retries
        store = {}

        def save(state):
            store[state.deployment_id] = state

        actions = []
        with patch.object(orchestrator, 'get_deployment_state',
                          side_effect=lambda deployment_id: store.get(deployment_id)), \
             patch.object(orchestrator, 'save_deployment_state', side_effect=save), \
             patch.object(orchestrator, 'get_previous_version', return_value='0.9.0'), \
             patch.object(orchestrator, 'get_live_version', return_value='1.0.0'), \
             patch.object(orchestrator, 'trigger_github_workflow_rerun',
                          new=AsyncMock(return_value=True)), \
             patch.object(orchestrator, 'rollback_deployment', return_value=True):
            for _ in failures:
                result = await orchestrator.handle_deployment_failure(
                    deployment_id='test-123',
                    namespace='production',
                    deployment_name='sample-app',
                    version='1.0.0',
                    failure_type=FailureType.DEPLOYMENT_FAILURE,
                    metadata={'workflow_run_id': '1'}
                )
                actions.append(result['action'])

        assert actions == expected


class TestLiveVersion:
    """Reading the running version from the Deployment"""

    @pytest.mark.parametrize('image,expected', [
        ('ghcr.io/o/r/sample-app:v1a2b3c4', 'v1a2b3c4'),
        ('registry.local:5000/sample-app:1.0.0', '1.0.0'),
        ('sample-app', None),
        ('sample-app@sha256:' + 'a' * 64, None),
        ('sample-app:1.0.0@sha256:' + 'a' * 64, None),
    ])
    def test_get_live_version_reads_the_tag(self, orchestrator, image, expected):
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = deployment_with(
            container('sample-app', image)
        )

        assert orchestrator.get_live_version('production', 'sample-app') == expected

    def test_get_live_version_uses_the_container_named_like_the_deployment(self, orchestrator):
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = deployment_with(
            container('istio-proxy', 'proxy:9.9.9'),
            container('sample-app', 'sample-app:1.0.0')
        )

        assert orchestrator.get_live_version('production', 'sample-app') == '1.0.0'

    def test_find_container_accepts_a_single_differently_named_container(self):
        only = container('web', 'sample-app:1.0.0')

        assert find_container(deployment_with(only), 'sample-app') is only

    def test_find_container_rejects_ambiguous_deployments(self):
        deployment = deployment_with(container('a', 'a:1'), container('b', 'b:1'))

        with pytest.raises(ContainerNotFoundError):
            find_container(deployment, 'sample-app')

    def test_find_container_rejects_deployments_without_containers(self):
        with pytest.raises(ContainerNotFoundError):
            find_container(deployment_with(), 'sample-app')


def serve(orchestrator, *containers, generation=7):
    """Make the fake API server return a Deployment and apply image patches"""
    orchestrator.k8s_apps.read_namespaced_deployment.return_value = deployment_with(*containers)

    def apply(name, namespace, body):
        patch_container = body['spec']['template']['spec']['containers'][0]
        patched = deployment_with(container(patch_container['name'], patch_container['image']))
        patched.metadata.generation = generation
        return patched

    orchestrator.k8s_apps.patch_namespaced_deployment.side_effect = apply


class TestRollbackDeployment:
    """Rollback patches only the image and waits for the new revision"""

    def roll_back(self, orchestrator, **kwargs):
        with patch.object(orchestrator, 'check_deployment_health', return_value=True) as health:
            result = orchestrator.rollback_deployment(
                'production', 'sample-app', '0.9.0', deployment_id='test-123', **kwargs
            )
        return result, health

    def test_patches_only_the_image_and_version_labels(self, orchestrator):
        serve(orchestrator, container('sample-app', 'ghcr.io/o/sample-app:1.0.0'))

        result, _ = self.roll_back(orchestrator)

        assert result is True
        body = orchestrator.k8s_apps.patch_namespaced_deployment.call_args.kwargs['body']
        assert body == {
            'metadata': {'labels': {'version': '0.9.0'}},
            'spec': {'template': {
                'metadata': {'labels': {'version': '0.9.0'}},
                'spec': {'containers': [
                    {'name': 'sample-app', 'image': 'ghcr.io/o/sample-app:0.9.0'}
                ]}
            }}
        }

    def test_waits_for_the_patched_generation(self, orchestrator):
        serve(orchestrator, container('sample-app', 'sample-app:1.0.0'), generation=12)

        _, health = self.roll_back(orchestrator)

        assert health.call_args.kwargs['expected_generation'] == 12

    @pytest.mark.parametrize('image,expected', [
        ('localhost:5000/sample-app:1.0.0', 'localhost:5000/sample-app:0.9.0'),
        ('localhost:5000/sample-app', 'localhost:5000/sample-app:0.9.0'),
        ('sample-app@sha256:' + 'a' * 64, 'sample-app:0.9.0'),
    ])
    def test_builds_the_target_image_from_any_reference_form(self, orchestrator, image, expected):
        serve(orchestrator, container('sample-app', image))

        result, _ = self.roll_back(orchestrator)

        assert result is True
        body = orchestrator.k8s_apps.patch_namespaced_deployment.call_args.kwargs['body']
        assert body['spec']['template']['spec']['containers'][0]['image'] == expected

    def test_patches_a_single_container_with_a_different_name(self, orchestrator):
        serve(orchestrator, container('web', 'sample-app:1.0.0'))

        result, _ = self.roll_back(orchestrator)

        assert result is True
        body = orchestrator.k8s_apps.patch_namespaced_deployment.call_args.kwargs['body']
        assert body['spec']['template']['spec']['containers'][0]['name'] == 'web'

    def test_fails_without_patching_when_the_container_is_ambiguous(self, orchestrator):
        serve(orchestrator, container('a', 'a:1'), container('b', 'b:1'))

        result, health = self.roll_back(orchestrator)

        assert result is False
        orchestrator.k8s_apps.patch_namespaced_deployment.assert_not_called()
        health.assert_not_called()

    def test_fails_when_the_patch_did_not_change_the_image(self, orchestrator):
        serve(orchestrator, container('sample-app', 'sample-app:1.0.0'))
        orchestrator.k8s_apps.patch_namespaced_deployment.side_effect = None
        orchestrator.k8s_apps.patch_namespaced_deployment.return_value = deployment_with(
            container('sample-app', 'sample-app:1.0.0')
        )

        result, health = self.roll_back(orchestrator)

        assert result is False
        health.assert_not_called()

    @pytest.mark.parametrize('error', [
        ApiException(status=409, reason='Conflict'),
        RuntimeError('connection reset'),
        TypeError('unexpected response'),
    ])
    def test_any_error_during_the_patch_is_a_failed_rollback(self, orchestrator, error):
        serve(orchestrator, container('sample-app', 'sample-app:1.0.0'))
        orchestrator.k8s_apps.patch_namespaced_deployment.side_effect = error

        result, health = self.roll_back(orchestrator)

        assert result is False
        health.assert_not_called()

    def test_unhealthy_rollout_is_a_failed_rollback(self, orchestrator):
        serve(orchestrator, container('sample-app', 'sample-app:1.0.0'))

        with patch.object(orchestrator, 'check_deployment_health', return_value=False):
            result = orchestrator.rollback_deployment(
                'production', 'sample-app', '0.9.0', deployment_id='test-123'
            )

        assert result is False

    def test_audit_trail_records_the_running_version_and_reason(self, orchestrator):
        serve(orchestrator, container('sample-app', 'sample-app:1.0.0'))

        with patch('src.orchestrator.audit_logger') as audit:
            self.roll_back(orchestrator, reason='manual rollback requested')

        initiated = audit.log_rollback_initiated.call_args.kwargs
        assert initiated['current_version'] == '1.0.0'
        assert initiated['reason'] == 'manual rollback requested'
        audit.log_rollback_completed.assert_called_once()

    def test_failure_is_audited_with_the_error_type(self, orchestrator):
        serve(orchestrator, container('a', 'a:1'), container('b', 'b:1'))

        with patch('src.orchestrator.audit_logger') as audit:
            self.roll_back(orchestrator)

        assert 'ContainerNotFoundError' in audit.log_rollback_failed.call_args.kwargs['error']


def rollout_status(*, generation=2, observed=2, replicas=3, updated=3, total=3, ready=3):
    deployment = Mock()
    deployment.spec.replicas = replicas
    deployment.status.observed_generation = observed
    deployment.status.updated_replicas = updated
    deployment.status.replicas = total
    deployment.status.ready_replicas = ready
    return deployment


class TestRolloutCompletion:
    """A rollback only counts once the new revision replaced the old one"""

    @pytest.mark.parametrize('kwargs,expected', [
        ({}, True),
        ({'observed': 1}, False),
        ({'updated': 1}, False),
        ({'total': 4}, False),
        ({'updated': None, 'total': None}, False),
    ])
    def test_rollout_complete(self, kwargs, expected):
        from src.orchestrator import rollout_complete

        assert rollout_complete(rollout_status(**kwargs), generation=2) is expected

    def test_old_ready_replicas_do_not_pass_the_health_check(self, orchestrator):
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = rollout_status(
            observed=1, updated=0, total=3, ready=3
        )

        with patch('src.orchestrator.time.sleep'), \
             patch.object(orchestrator, 'check_application_health', return_value=True) as app:
            healthy = orchestrator.check_deployment_health(
                'production', 'sample-app', timeout=0.2, expected_generation=2
            )

        assert healthy is False
        app.assert_not_called()

    def test_completed_rollout_passes_the_health_check(self, orchestrator):
        orchestrator.k8s_apps.read_namespaced_deployment.return_value = rollout_status()

        with patch.object(orchestrator, 'check_application_health', return_value=True):
            healthy = orchestrator.check_deployment_health(
                'production', 'sample-app', timeout=5, expected_generation=2
            )

        assert healthy is True


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