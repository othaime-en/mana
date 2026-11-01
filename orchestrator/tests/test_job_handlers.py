import time
from unittest.mock import Mock, patch

import pytest
import redis
import requests

from src import metrics
from src.jobs import Job, JobDeferred, PermanentJobError
from src.orchestrator import (
    ACTIVE_INDEX_KEY,
    DeploymentState,
    DeploymentStatus,
    FailureType,
    GitHubRetryableError,
    SelfHealingOrchestrator,
)
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis


@pytest.fixture
def orchestrator():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        orch = SelfHealingOrchestrator(use_config=False)
    orch.redis_client = FakeRedis()
    orch.jobs = FakeJobQueue()
    orch.locks = FakeLocks()
    orch.github_token = "token"
    orch.github_repo = "owner/repo"
    return orch


def save_state(orch, status=DeploymentStatus.ROLLING_BACK, deployment_id="d1"):
    state = DeploymentState(
        deployment_id=deployment_id,
        namespace="production",
        app_name="sample-app",
        version="v2",
        status=status,
        previous_version="v1",
        retry_count=4,
        failure_type=FailureType.HEALTH_CHECK_FAILURE,
        timestamp=time.time(),
        metadata={},
        started_at=time.time() - 30,
        first_failure_at=time.time() - 20,
    )
    orch.save_deployment_state(state)
    return state


def rollback_job(attempts=1, deployment_id="d1"):
    return Job(
        id=f"rollback:{deployment_id}:4",
        kind="rollback",
        payload={
            "deployment_id": deployment_id,
            "namespace": "production",
            "deployment_name": "sample-app",
            "version": "v2",
            "target_version": "v1",
        },
        attempts=attempts,
    )


def stored(orch, deployment_id="d1"):
    return orch.get_deployment_state(deployment_id)


def github_calls(code):
    return metrics.github_api_requests_total.labels(endpoint="rerun", code=code)._value.get()


class TestRerunWorkflow:
    def respond(self, status, text=""):
        return Mock(status_code=status, text=text)

    def test_accepted_rerun_returns_and_is_counted(self, orchestrator):
        before = github_calls("201")
        with patch("src.orchestrator.requests.post", return_value=self.respond(201)) as post:
            orchestrator.rerun_workflow("123")

        assert post.call_args.args[0] == "https://api.github.com/repos/owner/repo/actions/runs/123/rerun"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"
        assert github_calls("201") == before + 1

    @pytest.mark.parametrize("code", [403, 409, 429, 500, 502, 503])
    def test_codes_that_may_clear_up_are_retryable(self, orchestrator, code):
        with patch("src.orchestrator.requests.post", return_value=self.respond(code, "busy")):
            with pytest.raises(GitHubRetryableError, match=str(code)):
                orchestrator.rerun_workflow("123")

    @pytest.mark.parametrize("code", [400, 401, 404, 422])
    def test_other_client_errors_are_permanent(self, orchestrator, code):
        with patch("src.orchestrator.requests.post", return_value=self.respond(code, "nope")):
            with pytest.raises(PermanentJobError, match=str(code)):
                orchestrator.rerun_workflow("123")

    @pytest.mark.parametrize("error", [requests.exceptions.Timeout, requests.exceptions.ConnectionError])
    def test_network_failures_are_retryable_and_counted(self, orchestrator, error):
        before = github_calls("error")
        with patch("src.orchestrator.requests.post", side_effect=error("down")):
            with pytest.raises(GitHubRetryableError):
                orchestrator.rerun_workflow("123")

        assert github_calls("error") == before + 1

    def test_missing_github_configuration_is_permanent(self, orchestrator):
        orchestrator.github_token = None

        with patch("src.orchestrator.requests.post") as post:
            with pytest.raises(PermanentJobError, match="GITHUB_TOKEN"):
                orchestrator.rerun_workflow("123")

        post.assert_not_called()

    def retry_job(self, retry_count=4):
        return Job(
            f"retry_rerun:d1:{retry_count}", "retry_rerun",
            {"deployment_id": "d1", "workflow_run_id": "77", "retry_count": retry_count},
        )

    def test_the_retry_job_re_runs_the_recorded_workflow(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.RETRY_SCHEDULED)

        with patch.object(orchestrator, "rerun_workflow") as rerun:
            orchestrator.job_handlers()["retry_rerun"](self.retry_job())

        rerun.assert_called_once_with("77")

    @pytest.mark.parametrize("status", [
        DeploymentStatus.ROLLING_BACK, DeploymentStatus.SUCCESS, DeploymentStatus.IN_PROGRESS,
    ])
    def test_a_retry_job_is_skipped_once_the_deployment_moved_on(self, orchestrator, status):
        save_state(orchestrator, status)

        with patch.object(orchestrator, "rerun_workflow") as rerun:
            orchestrator.job_handlers()["retry_rerun"](self.retry_job())

        rerun.assert_not_called()

    def test_a_retry_job_of_an_earlier_failure_is_skipped(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.RETRY_SCHEDULED)

        with patch.object(orchestrator, "rerun_workflow") as rerun:
            orchestrator.job_handlers()["retry_rerun"](self.retry_job(retry_count=3))

        rerun.assert_not_called()

    def test_a_retry_job_without_state_is_skipped(self, orchestrator):
        with patch.object(orchestrator, "rerun_workflow") as rerun:
            orchestrator.job_handlers()["retry_rerun"](self.retry_job())

        rerun.assert_not_called()


class TestRollbackJob:
    def run(self, orchestrator, job=None, live="v2", rollback_result=True):
        with patch.object(orchestrator, "get_live_version", return_value=live), \
             patch.object(orchestrator, "rollback_deployment", return_value=rollback_result) as rollback:
            orchestrator.job_handlers()["rollback"](job or rollback_job())
        return rollback

    def test_a_successful_rollback_settles_the_deployment(self, orchestrator):
        save_state(orchestrator)
        counted = metrics.rollbacks_total.labels(
            namespace="production", reason="health_check_failure"
        )._value.get()

        rollback = self.run(orchestrator)

        rollback.assert_called_once_with("production", "sample-app", "v1", deployment_id="d1")
        assert stored(orchestrator).status is DeploymentStatus.ROLLED_BACK
        assert stored(orchestrator).finished_at is not None
        assert metrics.rollbacks_total.labels(
            namespace="production", reason="health_check_failure"
        )._value.get() == counted + 1
        assert orchestrator.redis_client.sorted_sets.get(ACTIVE_INDEX_KEY, {}) == {}

    def test_a_failed_rollback_marks_the_deployment_failed(self, orchestrator):
        save_state(orchestrator)

        self.run(orchestrator, rollback_result=False)

        assert stored(orchestrator).status is DeploymentStatus.FAILED

    def test_the_lock_is_released_afterwards(self, orchestrator):
        save_state(orchestrator)

        self.run(orchestrator)

        assert orchestrator.locks.held == set()

    def test_the_lock_is_released_when_the_rollback_raises(self, orchestrator):
        save_state(orchestrator)
        with patch.object(orchestrator, "get_live_version", return_value="v2"), \
             patch.object(orchestrator, "rollback_deployment", side_effect=redis.ConnectionError("down")):
            with pytest.raises(redis.ConnectionError):
                orchestrator.job_handlers()["rollback"](rollback_job())

        assert orchestrator.locks.held == set()

    def test_a_held_lock_defers_the_job_without_touching_the_cluster(self, orchestrator):
        save_state(orchestrator)
        orchestrator.locks.acquire("production", "sample-app")

        with patch.object(orchestrator, "get_live_version") as live, \
             patch.object(orchestrator, "rollback_deployment") as rollback:
            with pytest.raises(JobDeferred):
                orchestrator.job_handlers()["rollback"](rollback_job())

        live.assert_not_called()
        rollback.assert_not_called()
        assert stored(orchestrator).status is DeploymentStatus.ROLLING_BACK

    @pytest.mark.parametrize("status", [
        DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED, DeploymentStatus.SUCCESS,
    ])
    def test_a_job_for_a_settled_deployment_does_nothing(self, orchestrator, status):
        save_state(orchestrator, status)

        rollback = self.run(orchestrator)

        rollback.assert_not_called()
        assert stored(orchestrator).status is status

    def test_expired_state_is_ignored(self, orchestrator):
        rollback = self.run(orchestrator)

        rollback.assert_not_called()

    def test_another_live_version_skips_the_rollback(self, orchestrator):
        save_state(orchestrator)

        rollback = self.run(orchestrator, live="v3")

        rollback.assert_not_called()
        assert stored(orchestrator).status is DeploymentStatus.FAILED

    def test_a_first_attempt_that_finds_the_target_live_skips_it(self, orchestrator):
        save_state(orchestrator)

        rollback = self.run(orchestrator, live="v1", job=rollback_job(attempts=1))

        rollback.assert_not_called()
        assert stored(orchestrator).status is DeploymentStatus.FAILED

    def test_a_retried_attempt_that_finds_the_target_live_resumes_and_verifies(self, orchestrator):
        save_state(orchestrator)

        rollback = self.run(orchestrator, live="v1", job=rollback_job(attempts=2))

        rollback.assert_called_once()
        assert stored(orchestrator).status is DeploymentStatus.ROLLED_BACK

    def test_an_unreadable_live_version_raises_an_alert_instead_of_rolling_back(self, orchestrator):
        save_state(orchestrator)

        rollback = self.run(orchestrator, live=None)

        rollback.assert_not_called()
        assert stored(orchestrator).status is DeploymentStatus.FAILED

    def test_a_redis_error_propagates_so_the_job_is_retried(self, orchestrator):
        orchestrator.redis_client.get = Mock(side_effect=redis.ConnectionError("down"))

        with pytest.raises(redis.ConnectionError):
            orchestrator.job_handlers()["rollback"](rollback_job())

        assert orchestrator.locks.held == set()


class TestDeadJobs:
    def test_a_deployment_waiting_for_the_dead_rollback_is_marked_failed(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.ROLLING_BACK)

        orchestrator.handle_dead_job(rollback_job(attempts=5), RuntimeError("api down"))

        assert stored(orchestrator).status is DeploymentStatus.FAILED
        assert orchestrator.redis_client.sorted_sets.get(ACTIVE_INDEX_KEY, {}) == {}

    def test_a_deployment_waiting_for_the_dead_retry_is_marked_failed(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.RETRY_SCHEDULED)
        job = Job("retry_rerun:d1:4", "retry_rerun", {"deployment_id": "d1", "workflow_run_id": "1", "retry_count": 4})

        orchestrator.handle_dead_job(job, RuntimeError("github down"))

        assert stored(orchestrator).status is DeploymentStatus.FAILED

    def test_a_dead_retry_does_not_touch_a_deployment_that_moved_on(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.ROLLING_BACK)
        job = Job("retry_rerun:d1:1", "retry_rerun", {"deployment_id": "d1", "workflow_run_id": "1", "retry_count": 1})

        orchestrator.handle_dead_job(job, RuntimeError("github down"))

        assert stored(orchestrator).status is DeploymentStatus.ROLLING_BACK

    def test_a_settled_deployment_keeps_its_status(self, orchestrator):
        save_state(orchestrator, DeploymentStatus.ROLLED_BACK)

        orchestrator.handle_dead_job(rollback_job(attempts=5), RuntimeError("late"))

        assert stored(orchestrator).status is DeploymentStatus.ROLLED_BACK

    def test_missing_state_is_tolerated(self, orchestrator):
        orchestrator.handle_dead_job(rollback_job(), RuntimeError("x"))

    def test_the_audit_trail_names_the_job_and_the_error(self, orchestrator):
        save_state(orchestrator)

        with patch("src.orchestrator.audit_logger") as audit:
            orchestrator.handle_dead_job(rollback_job(attempts=5), RuntimeError("api down"))

        reason = audit.log_manual_intervention_required.call_args.kwargs["reason"]
        assert "rollback job abandoned after 5 attempts" in reason and "api down" in reason
