from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def api():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_incluster_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        from src import api as api_module
    return api_module
