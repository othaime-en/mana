import os
from unittest.mock import Mock, patch

import pytest

from src.jobs import QueueStats

os.environ.setdefault("AUTH_DISABLED", "true")


@pytest.fixture(scope="module")
def api():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_incluster_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"), \
         patch("src.orchestrator.client.VersionApi"):
        from src import api as api_module
    api_module.orchestrator.jobs = Mock(**{"stats.return_value": QueueStats()})
    return api_module


REDIS_TEST_URL = os.environ.get("MANA_TEST_REDIS_URL", "redis://localhost:6390/15")


@pytest.fixture
def real_redis():
    """A real Redis for the tests that rely on Lua scripts and cross-process behaviour

    Skips when none is reachable, unless REQUIRE_REDIS_TESTS=1 (set in CI), so a
    missing service cannot turn these tests into silent no-ops.
    """
    import redis

    client = redis.Redis.from_url(REDIS_TEST_URL, decode_responses=True, socket_timeout=5)
    try:
        client.ping()
    except redis.RedisError as error:
        if os.environ.get("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail(f"REQUIRE_REDIS_TESTS=1 but Redis is unreachable at {REDIS_TEST_URL}: {error}")
        pytest.skip(f"no Redis at {REDIS_TEST_URL} (set MANA_TEST_REDIS_URL)")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture(autouse=True)
def replica_is_not_draining(request):
    """Lifespan tests flip the draining flag; start every test with it cleared"""
    if "api" in request.fixturenames:
        request.getfixturevalue("api").app.state.draining = False
