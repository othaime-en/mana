import pytest
from unittest.mock import Mock, patch
from src.orchestrator import (
    SelfHealingOrchestrator
)


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