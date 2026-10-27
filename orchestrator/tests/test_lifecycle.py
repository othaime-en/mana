import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
import redis

from src.orchestrator import (
    ACTIVE_INDEX_KEY,
    STATE_TTL_SECONDS,
    DeploymentState,
    DeploymentStatus,
    FailureType,
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


def start(orch, deployment_id="deploy-1", namespace="production", **kwargs):
    return orch.record_deployment_started(
        deployment_id, namespace, "sample-app", "v1", **kwargs
    )


class TestRecordDeploymentStarted:
    def test_persists_an_in_progress_state(self, orchestrator):
        before = time.time()

        transition = start(orchestrator, metadata={"commit": "abc"})

        state = orchestrator.get_deployment_state("deploy-1")
        assert transition.previous_status is None
        assert state.status is DeploymentStatus.IN_PROGRESS
        assert before <= state.started_at <= time.time()
        assert state.retry_count == 0
        assert state.previous_version is None
        assert state.finished_at is None
        assert state.metadata == {"commit": "abc"}

    def test_marks_the_deployment_active(self, orchestrator):
        start(orchestrator)

        assert orchestrator.active_deployment_counts() == {"production": 1}

    def test_rerun_reopens_state_and_keeps_history(self, orchestrator):
        start(orchestrator)
        state = orchestrator.get_deployment_state("deploy-1")
        state.status = DeploymentStatus.RETRY_SCHEDULED
        state.retry_count = 2
        state.previous_version = "v0"
        state.first_failure_at = 123.0
        orchestrator.save_deployment_state(state)
        first_started_at = state.started_at

        transition = start(orchestrator, metadata={"run_attempt": "2"})

        reopened = orchestrator.get_deployment_state("deploy-1")
        assert transition.previous_status is DeploymentStatus.RETRY_SCHEDULED
        assert reopened.status is DeploymentStatus.IN_PROGRESS
        assert reopened.retry_count == 2
        assert reopened.previous_version == "v0"
        assert reopened.first_failure_at == 123.0
        assert reopened.started_at == first_started_at
        assert reopened.metadata["run_attempt"] == "2"
        assert orchestrator.active_deployment_counts() == {"production": 1}

    def test_reopening_a_finished_deployment_clears_finished_at(self, orchestrator):
        start(orchestrator)
        state = orchestrator.get_deployment_state("deploy-1")
        state.status = DeploymentStatus.FAILED
        orchestrator.save_deployment_state(state)
        assert orchestrator.get_deployment_state("deploy-1").finished_at is not None
        assert orchestrator.active_deployment_counts() == {}

        start(orchestrator)

        reopened = orchestrator.get_deployment_state("deploy-1")
        assert reopened.finished_at is None
        assert orchestrator.active_deployment_counts() == {"production": 1}


class TestRecordDeploymentSucceeded:
    def succeed(self, orch, deployment_id="deploy-1"):
        return orch.record_deployment_succeeded(deployment_id, "production", "sample-app", "v1")

    def test_stamps_finish_time_and_leaves_the_active_index(self, orchestrator):
        start(orchestrator)

        transition = self.succeed(orchestrator)

        state = orchestrator.get_deployment_state("deploy-1")
        assert transition.previous_status is DeploymentStatus.IN_PROGRESS
        assert state.status is DeploymentStatus.SUCCESS
        assert state.finished_at >= state.started_at
        assert orchestrator.active_deployment_counts() == {}

    def test_remembers_the_version_as_rollback_target(self, orchestrator):
        self.succeed(orchestrator)

        assert orchestrator.redis_client.get("last_successful:production:sample-app") == "v1"

    def test_duplicate_success_keeps_the_original_finish_time(self, orchestrator):
        start(orchestrator)
        self.succeed(orchestrator)
        finished_at = orchestrator.get_deployment_state("deploy-1").finished_at

        transition = self.succeed(orchestrator)

        assert transition.previous_status is DeploymentStatus.SUCCESS
        assert orchestrator.get_deployment_state("deploy-1").finished_at == finished_at

    def test_success_without_a_prior_event_creates_a_state(self, orchestrator):
        transition = self.succeed(orchestrator)

        state = orchestrator.get_deployment_state("deploy-1")
        assert transition.previous_status is None
        assert state.status is DeploymentStatus.SUCCESS
        assert state.started_at is None
        assert state.finished_at is not None


class TestFailureLifecycle:
    @pytest.mark.asyncio
    async def test_first_failure_is_stamped_once_and_started_at_survives(self, orchestrator):
        start(orchestrator)
        started_at = orchestrator.get_deployment_state("deploy-1").started_at
        orchestrator.get_previous_version = Mock(return_value="v0")
        orchestrator.trigger_github_workflow_rerun = AsyncMock()

        for _ in range(2):
            await orchestrator.handle_deployment_failure(
                "deploy-1", "production", "sample-app", "v1",
                FailureType.HEALTH_CHECK_FAILURE, {"workflow_run_id": "7"},
            )
            first_failure_at = orchestrator.get_deployment_state("deploy-1").first_failure_at

        state = orchestrator.get_deployment_state("deploy-1")
        assert state.started_at == started_at
        assert state.first_failure_at == first_failure_at
        assert state.first_failure_at >= started_at
        assert state.retry_count == 2

    @pytest.mark.asyncio
    async def test_previous_version_is_resolved_at_failure_time_only_once(self, orchestrator):
        start(orchestrator)
        orchestrator.get_previous_version = Mock(return_value="v0")
        orchestrator.trigger_github_workflow_rerun = AsyncMock()

        for _ in range(2):
            await orchestrator.handle_deployment_failure(
                "deploy-1", "production", "sample-app", "v1",
                FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "7"},
            )

        assert orchestrator.get_previous_version.call_count == 1
        assert orchestrator.get_deployment_state("deploy-1").previous_version == "v0"

    @pytest.mark.asyncio
    async def test_failure_without_prior_in_progress_still_works(self, orchestrator):
        orchestrator.get_previous_version = Mock(return_value="v0")
        orchestrator.trigger_github_workflow_rerun = AsyncMock()

        result = await orchestrator.handle_deployment_failure(
            "deploy-1", "production", "sample-app", "v1",
            FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "7"},
        )

        state = orchestrator.get_deployment_state("deploy-1")
        assert result["action"] == "retry"
        assert state.started_at is None
        assert state.first_failure_at is not None

    @pytest.mark.asyncio
    async def test_retry_keeps_the_deployment_active_and_alert_ends_it(self, orchestrator):
        start(orchestrator)
        orchestrator.get_previous_version = Mock(return_value="v0")
        orchestrator.trigger_github_workflow_rerun = AsyncMock()

        await orchestrator.handle_deployment_failure(
            "deploy-1", "production", "sample-app", "v1",
            FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "7"},
        )
        assert orchestrator.active_deployment_counts() == {"production": 1}

        orchestrator.max_retries = 1
        await orchestrator.handle_deployment_failure(
            "deploy-1", "production", "sample-app", "v1",
            FailureType.BUILD_FAILURE, {"workflow_run_id": "7"},
        )

        state = orchestrator.get_deployment_state("deploy-1")
        assert state.status is DeploymentStatus.FAILED
        assert state.finished_at is not None
        assert orchestrator.active_deployment_counts() == {}


class TestActiveIndex:
    def test_counts_are_grouped_by_namespace(self, orchestrator):
        start(orchestrator, "a", "production")
        start(orchestrator, "b", "production")
        start(orchestrator, "c", "staging")

        assert orchestrator.active_deployment_counts() == {"production": 2, "staging": 1}

    def test_entries_older_than_the_state_ttl_are_dropped(self, orchestrator):
        start(orchestrator, "fresh")
        stale = time.time() - STATE_TTL_SECONDS - 60
        orchestrator.redis_client.zadd(ACTIVE_INDEX_KEY, {"production|stale": stale})

        assert orchestrator.active_deployment_counts() == {"production": 1}
        assert "production|stale" not in orchestrator.redis_client.sorted_sets[ACTIVE_INDEX_KEY]

    def test_index_failure_does_not_fail_the_save(self, orchestrator):
        orchestrator.redis_client.zadd = Mock(side_effect=redis.ConnectionError("down"))

        start(orchestrator)

        assert orchestrator.get_deployment_state("deploy-1") is not None


class TestStateCompatibility:
    def legacy_document(self, **extra):
        document = {
            "deployment_id": "old",
            "namespace": "production",
            "app_name": "sample-app",
            "version": "v1",
            "status": "retry_scheduled",
            "previous_version": "v0",
            "retry_count": 1,
            "failure_type": "deployment_failure",
            "timestamp": 1.0,
            "metadata": {},
        }
        document.update(extra)
        return json.dumps(document)

    def test_state_saved_before_the_lifecycle_fields_still_loads(self, orchestrator):
        orchestrator.redis_client.set("deployment:old", self.legacy_document())

        state = orchestrator.get_deployment_state("old")

        assert isinstance(state, DeploymentState)
        assert state.started_at is None
        assert state.first_failure_at is None
        assert state.finished_at is None

    def test_unknown_fields_from_a_newer_version_are_ignored(self, orchestrator):
        orchestrator.redis_client.set("deployment:old", self.legacy_document(diagnosis="x"))

        assert orchestrator.get_deployment_state("old").deployment_id == "old"
