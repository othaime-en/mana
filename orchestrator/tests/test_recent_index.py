import time
from unittest.mock import patch

import pytest
import redis

from src.orchestrator import (
    RECENT_INDEX_KEY,
    RECENT_INDEX_LIMIT,
    STATE_TTL_SECONDS,
    DeploymentState,
    DeploymentStatus,
    SelfHealingOrchestrator,
)
from tests.fakes import FakeRedis


@pytest.fixture
def orchestrator():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        orch = SelfHealingOrchestrator(use_config=False)
    orch.redis_client = FakeRedis()
    return orch


def save(orch, deployment_id, namespace="production", timestamp=None, status=DeploymentStatus.IN_PROGRESS):
    state = DeploymentState(
        deployment_id=deployment_id,
        namespace=namespace,
        app_name="sample-app",
        version="v1",
        status=status,
        previous_version=None,
        retry_count=0,
        failure_type=None,
        timestamp=time.time() if timestamp is None else timestamp,
        metadata={},
    )
    orch.save_deployment_state(state)
    return state


def test_every_saved_state_enters_the_index(orchestrator):
    save(orchestrator, "deploy-1")

    assert orchestrator.redis_client.sorted_sets[RECENT_INDEX_KEY].keys() == {"production|deploy-1"}


def test_resaving_a_state_keeps_one_index_entry(orchestrator):
    state = save(orchestrator, "deploy-1")
    state.status = DeploymentStatus.SUCCESS
    orchestrator.save_deployment_state(state)

    assert len(orchestrator.redis_client.sorted_sets[RECENT_INDEX_KEY]) == 1


def test_recent_deployments_come_newest_first(orchestrator):
    now = time.time()
    save(orchestrator, "old", timestamp=now - 30)
    save(orchestrator, "new", timestamp=now - 1)
    save(orchestrator, "middle", timestamp=now - 10)

    ids = [s.deployment_id for s in orchestrator.get_recent_deployments()]

    assert ids == ["new", "middle", "old"]


def test_limit_caps_the_result(orchestrator):
    now = time.time()
    for i in range(5):
        save(orchestrator, f"d{i}", timestamp=now - i)

    assert [s.deployment_id for s in orchestrator.get_recent_deployments(limit=2)] == ["d0", "d1"]


def test_namespace_filter_applies_before_the_limit(orchestrator):
    now = time.time()
    save(orchestrator, "p1", "production", now - 1)
    save(orchestrator, "s1", "staging", now - 2)
    save(orchestrator, "s2", "staging", now - 3)
    save(orchestrator, "p2", "production", now - 4)

    result = orchestrator.get_recent_deployments(namespace="staging", limit=5)

    assert [s.deployment_id for s in result] == ["s1", "s2"]


def test_expired_states_are_skipped_and_dropped_from_the_index(orchestrator):
    save(orchestrator, "gone")
    save(orchestrator, "kept")
    del orchestrator.redis_client.data["deployment:gone"]

    result = orchestrator.get_recent_deployments()

    assert [s.deployment_id for s in result] == ["kept"]
    assert "production|gone" not in orchestrator.redis_client.sorted_sets[RECENT_INDEX_KEY]


def test_unreadable_state_does_not_hide_the_others(orchestrator):
    save(orchestrator, "bad")
    save(orchestrator, "good")
    orchestrator.redis_client.data["deployment:bad"] = "{not json"

    assert [s.deployment_id for s in orchestrator.get_recent_deployments()] == ["good"]


def test_index_is_capped(orchestrator):
    now = time.time()
    for i in range(RECENT_INDEX_LIMIT + 5):
        save(orchestrator, f"d{i}", timestamp=now - 1000 + i)

    index = orchestrator.redis_client.sorted_sets[RECENT_INDEX_KEY]

    assert len(index) == RECENT_INDEX_LIMIT
    assert "production|d0" not in index
    assert f"production|d{RECENT_INDEX_LIMIT + 4}" in index


def test_entries_older_than_the_state_ttl_are_trimmed_on_save(orchestrator):
    save(orchestrator, "ancient", timestamp=time.time() - STATE_TTL_SECONDS - 60)
    save(orchestrator, "fresh")

    assert orchestrator.redis_client.sorted_sets[RECENT_INDEX_KEY].keys() == {"production|fresh"}


def test_history_never_scans_the_keyspace(orchestrator):
    orchestrator.redis_client.keys = lambda *_: pytest.fail("KEYS must not be used")
    save(orchestrator, "deploy-1")

    assert len(orchestrator.get_recent_deployments()) == 1


def test_index_failure_does_not_lose_the_state(orchestrator):
    def broken(*_args, **_kwargs):
        raise redis.RedisError("boom")

    orchestrator.redis_client.zadd = lambda key, mapping: (
        broken() if key == RECENT_INDEX_KEY else FakeRedis.zadd(orchestrator.redis_client, key, mapping)
    )

    save(orchestrator, "deploy-1")

    assert orchestrator.get_deployment_state("deploy-1") is not None
