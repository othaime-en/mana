"""The recovery path driven by the policy: fast rollback, diagnosis, fallbacks"""

import json
import logging
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest
from kubernetes.client.rest import ApiException
from prometheus_client import REGISTRY

from src.diagnosis import Diagnosis
from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator
from src.policy import DEFAULT_POLICY, Policy
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis, retry_policy

NS_NAME = "production"


@pytest.fixture
def orch():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        instance = SelfHealingOrchestrator(use_config=False)
    instance.redis_client = FakeRedis()
    instance.jobs = FakeJobQueue()
    instance.locks = FakeLocks()
    instance.get_previous_version = Mock(return_value="v1")
    instance.get_live_version = Mock(return_value="v2")
    instance.diagnose_failure = Mock(return_value=None)
    return instance


async def fail(orch, failure_type, deployment_id="d1", namespace=NS_NAME, version="v2", metadata=None):
    return await orch.handle_deployment_failure(
        deployment_id, namespace, "sample-app", version, failure_type,
        {"workflow_run_id": "9"} if metadata is None else metadata,
    )


def counter(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [FailureType.HEALTH_CHECK_FAILURE, FailureType.SMOKE_TEST_FAILURE])
async def test_a_post_deploy_failure_rolls_back_on_the_first_failure(orch, failure_type):
    result = await fail(orch, failure_type)

    assert result["action"] == "rollback"
    (job,) = orch.jobs.of_kind("rollback")
    assert job["delay"] == 0
    assert job["payload"]["target_version"] == "v1"
    assert orch.get_deployment_state("d1").status is DeploymentStatus.ROLLING_BACK
    assert orch.jobs.of_kind("retry_rerun") == []


@pytest.mark.asyncio
async def test_the_rollback_job_carries_the_policy_verification_timeout(orch):
    orch.policy = Policy.model_validate({**DEFAULT_POLICY, "rollback": {"verify_timeout_seconds": 45}})

    await fail(orch, FailureType.HEALTH_CHECK_FAILURE)

    assert orch.jobs.of_kind("rollback")[0]["payload"]["verify_timeout_seconds"] == 45


@pytest.mark.asyncio
async def test_a_deployment_failure_is_retried_once_and_then_rolled_back(orch):
    first = await fail(orch, FailureType.DEPLOYMENT_FAILURE)
    second = await fail(orch, FailureType.DEPLOYMENT_FAILURE)

    assert (first["action"], second["action"]) == ("retry", "rollback")


@pytest.mark.asyncio
async def test_a_test_failure_alerts_without_retrying(orch):
    result = await fail(orch, FailureType.TEST_FAILURE)

    assert result["action"] == "alert"
    assert "nothing to roll back" in result["message"]
    assert orch.jobs.jobs == []


@pytest.mark.asyncio
async def test_max_retries_remains_a_ceiling_on_the_policy(orch):
    orch.policy = retry_policy(attempts=5)
    orch.max_retries = 2

    actions = [(await fail(orch, FailureType.DEPLOYMENT_FAILURE))["action"] for _ in range(3)]

    assert actions == ["retry", "retry", "rollback"]


@pytest.mark.asyncio
async def test_a_failure_without_a_workflow_run_rolls_back_instead_of_alerting(orch):
    result = await fail(orch, FailureType.DEPLOYMENT_FAILURE, metadata={})

    assert result["action"] == "rollback"


@pytest.mark.asyncio
async def test_a_build_failure_without_a_workflow_run_alerts(orch):
    result = await fail(orch, FailureType.BUILD_FAILURE, metadata={})

    assert result["action"] == "alert"
    assert "nothing to roll back" in result["message"]


@pytest.mark.asyncio
async def test_overrides_apply_to_their_namespace_only(orch):
    orch.policy = Policy.model_validate({**DEFAULT_POLICY, "overrides": [{
        "match": {"namespace": "staging"},
        "rules": {"health_check_failure": {"action": "retry", "max_attempts": 2, "then": "rollback"}},
    }]})

    staging = await fail(orch, FailureType.HEALTH_CHECK_FAILURE, "d1", namespace="staging")
    production = await fail(orch, FailureType.HEALTH_CHECK_FAILURE, "d2")

    assert (staging["action"], production["action"]) == ("retry", "rollback")


@pytest.mark.asyncio
async def test_a_deterministic_diagnosis_skips_the_retry(orch):
    orch.diagnose_failure.return_value = Diagnosis("CrashLoopBackOff", False)

    result = await fail(orch, FailureType.DEPLOYMENT_FAILURE)

    assert result["action"] == "rollback"
    assert orch.get_deployment_state("d1").diagnosis == "CrashLoopBackOff"


@pytest.mark.asyncio
async def test_a_transient_diagnosis_keeps_the_retry(orch):
    orch.diagnose_failure.return_value = Diagnosis("ImagePullBackOff", True)

    result = await fail(orch, FailureType.DEPLOYMENT_FAILURE)

    assert result["action"] == "retry"
    assert orch.get_deployment_state("d1").diagnosis == "ImagePullBackOff"


@pytest.mark.asyncio
async def test_a_diagnosis_is_counted_and_named_in_the_alert(orch):
    orch.policy = Policy.model_validate({**DEFAULT_POLICY, "default": {
        **DEFAULT_POLICY["default"], "deployment_failure": {"action": "alert"}}})
    orch.diagnose_failure.return_value = Diagnosis("OOMKilled", False)
    before = counter("mana_failure_diagnoses_total", namespace="diag-ns", reason="OOMKilled")

    result = await fail(orch, FailureType.DEPLOYMENT_FAILURE, namespace="diag-ns")

    assert counter("mana_failure_diagnoses_total", namespace="diag-ns", reason="OOMKilled") == before + 1
    assert "(diagnosis: OOMKilled)" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [FailureType.BUILD_FAILURE, FailureType.TEST_FAILURE])
async def test_nothing_is_diagnosed_before_anything_is_deployed(orch, failure_type):
    await fail(orch, failure_type)

    orch.diagnose_failure.assert_not_called()


@pytest.mark.asyncio
async def test_a_rollback_to_the_failed_version_itself_becomes_an_alert(orch):
    orch.get_previous_version.return_value = "v2"

    result = await fail(orch, FailureType.HEALTH_CHECK_FAILURE)

    assert result["action"] == "alert"
    assert "failed version" in result["message"]
    assert orch.jobs.jobs == []


@pytest.mark.asyncio
async def test_a_rollback_without_any_known_version_alerts(orch):
    orch.get_previous_version.return_value = None

    result = await fail(orch, FailureType.HEALTH_CHECK_FAILURE)

    assert result["action"] == "alert"
    assert "No previous version" in result["message"]


@pytest.mark.asyncio
async def test_the_live_version_guard_still_applies_to_a_fast_rollback(orch):
    orch.get_live_version.return_value = "v3"

    result = await fail(orch, FailureType.HEALTH_CHECK_FAILURE)

    assert result["action"] == "skip"
    assert orch.jobs.jobs == []


@pytest.mark.asyncio
async def test_every_decision_is_counted_and_audited(orch, caplog):
    labels = dict(failure_type="health_check_failure", action="rollback", reason="policy")
    before = counter("mana_policy_decisions_total", **labels)

    with caplog.at_level(logging.INFO):
        await fail(orch, FailureType.HEALTH_CHECK_FAILURE)

    assert counter("mana_policy_decisions_total", **labels) == before + 1
    assert "AUDIT: policy_decision" in caplog.text


@pytest.mark.asyncio
async def test_the_detection_time_is_observed_once_per_deployment(orch):
    def seen(namespace):
        return counter("mana_detection_duration_seconds_count", namespace=namespace, app="sample-app")

    orch.record_deployment_started("d1", "detect-ns", "sample-app", "v2")
    state = orch.get_deployment_state("d1")
    state.started_at = time.time() - 42
    orch.save_deployment_state(state)

    await fail(orch, FailureType.DEPLOYMENT_FAILURE, namespace="detect-ns")
    await fail(orch, FailureType.DEPLOYMENT_FAILURE, namespace="detect-ns")

    assert seen("detect-ns") == 1
    total = counter("mana_detection_duration_seconds_sum", namespace="detect-ns", app="sample-app")
    assert 41 <= total <= 60


@pytest.mark.asyncio
async def test_no_detection_time_without_a_recorded_start(orch):
    await fail(orch, FailureType.DEPLOYMENT_FAILURE, "d9", namespace="nostart-ns")

    assert counter("mana_detection_duration_seconds_count", namespace="nostart-ns", app="sample-app") == 0


class TestDiagnoseFailure:
    @pytest.fixture
    def cluster(self):
        with patch("src.orchestrator.redis.Redis"), \
             patch("src.orchestrator.k8s_config.load_kube_config"), \
             patch("src.orchestrator.client.AppsV1Api") as apps, \
             patch("src.orchestrator.client.CoreV1Api") as core:
            instance = SelfHealingOrchestrator(use_config=False)
        instance.k8s_apps, instance.k8s_core = apps.return_value, core.return_value
        return instance

    def crashing_pod(self, image="r/app:v2"):
        status = NS(
            name="app",
            state=NS(waiting=NS(reason="CrashLoopBackOff", message=""), terminated=None),
            last_state=NS(terminated=None),
        )
        return NS(
            metadata=NS(name="app-1"),
            spec=NS(containers=[NS(image=image)]),
            status=NS(container_statuses=[status], conditions=[]),
        )

    def test_it_reads_the_pods_of_the_application(self, cluster):
        cluster.k8s_core.list_namespaced_pod.return_value = NS(items=[self.crashing_pod()])
        cluster.k8s_apps.read_namespaced_deployment.return_value = NS(status=NS(conditions=[]))

        result = cluster.diagnose_failure("production", "sample-app", "v2")

        assert result.reason == "CrashLoopBackOff"
        cluster.k8s_core.list_namespaced_pod.assert_called_once_with(
            namespace="production", label_selector="app=sample-app"
        )

    def test_pods_of_another_version_are_ignored(self, cluster):
        cluster.k8s_core.list_namespaced_pod.return_value = NS(items=[self.crashing_pod("r/app:v1")])
        cluster.k8s_apps.read_namespaced_deployment.return_value = NS(status=NS(conditions=[]))

        assert cluster.diagnose_failure("production", "sample-app", "v2") is None

    @pytest.mark.parametrize("error", [ApiException(status=403), ConnectionError("down"), TypeError("odd")])
    def test_trouble_reading_the_cluster_means_no_diagnosis(self, cluster, error):
        cluster.k8s_core.list_namespaced_pod.side_effect = error

        assert cluster.diagnose_failure("production", "sample-app", "v2") is None


class TestRollbackVerification:
    def test_the_policy_timeout_reaches_the_health_check(self, orch):
        from src.jobs import Job

        orch.redis_client = FakeRedis()
        orch.record_deployment_started("d1", NS_NAME, "sample-app", "v2")
        state = orch.get_deployment_state("d1")
        state.status = DeploymentStatus.ROLLING_BACK
        state.failure_type = FailureType.HEALTH_CHECK_FAILURE
        orch.save_deployment_state(state)
        job = Job(id="j", kind="rollback", attempts=1, payload={
            "deployment_id": "d1", "namespace": NS_NAME, "deployment_name": "sample-app",
            "version": "v2", "target_version": "v1", "verify_timeout_seconds": 77,
        })

        with patch.object(orch, "rollback_deployment", return_value=True) as rollback:
            orch._run_rollback_job(job)

        assert rollback.call_args.kwargs["timeout"] == 77

    def test_the_health_loop_polls_at_the_configured_interval(self, orch):
        orch.health_poll_seconds = 2.5
        deployment = NS(
            spec=NS(replicas=1),
            status=NS(ready_replicas=0, observed_generation=1, updated_replicas=0, replicas=0),
        )
        orch.k8s_apps = Mock(**{"read_namespaced_deployment.return_value": deployment})

        with patch("src.orchestrator.time.sleep", side_effect=InterruptedError) as sleep:
            with pytest.raises(InterruptedError):
                orch.check_deployment_health(NS_NAME, "sample-app", timeout=30)

        sleep.assert_called_once_with(2.5)


class TestAttemptsPerFailureType:
    """A rule's max_attempts counts failures of its own type (CORE-19)"""

    @pytest.mark.asyncio
    async def test_another_type_does_not_use_up_the_attempts(self, orch):
        results = [
            await fail(orch, FailureType.BUILD_FAILURE),
            await fail(orch, FailureType.DEPLOYMENT_FAILURE),
            await fail(orch, FailureType.DEPLOYMENT_FAILURE),
        ]

        assert [r["action"] for r in results] == ["retry", "retry", "rollback"]

    @pytest.mark.asyncio
    async def test_the_state_keeps_both_the_total_and_the_per_type_counts(self, orch):
        await fail(orch, FailureType.BUILD_FAILURE)
        await fail(orch, FailureType.DEPLOYMENT_FAILURE)
        await fail(orch, FailureType.DEPLOYMENT_FAILURE)

        state = orch.get_deployment_state("d1")

        assert state.retry_count == 3
        assert state.attempts == {"build_failure": 1, "deployment_failure": 2}

    @pytest.mark.asyncio
    async def test_the_backoff_and_message_follow_the_type_not_the_total(self, orch):
        orch.policy = retry_policy(attempts=3)
        await fail(orch, FailureType.BUILD_FAILURE)

        result = await fail(orch, FailureType.DEPLOYMENT_FAILURE)

        assert result["backoff_seconds"] == orch.initial_backoff
        assert result["message"].startswith("Retry 1/3")
        assert result["retry_count"] == 2

    @pytest.mark.asyncio
    async def test_the_policy_decision_audits_the_attempt_of_its_type(self, orch, caplog):
        await fail(orch, FailureType.BUILD_FAILURE)

        with caplog.at_level(logging.INFO):
            await fail(orch, FailureType.DEPLOYMENT_FAILURE)

        decision = [r for r in caplog.records if "AUDIT: policy_decision" in r.getMessage()][-1]
        assert decision.audit_event["details"]["attempt"] == 1

    @pytest.mark.asyncio
    async def test_a_state_saved_before_attempts_existed_is_backfilled(self, orch):
        await fail(orch, FailureType.DEPLOYMENT_FAILURE)
        state = orch.get_deployment_state("d1")
        state.attempts = {}
        state.retry_count = 1
        orch.save_deployment_state(state)

        second = await fail(orch, FailureType.DEPLOYMENT_FAILURE)

        assert second["action"] == "rollback"
        assert orch.get_deployment_state("d1").attempts == {"deployment_failure": 2}

    @pytest.mark.asyncio
    async def test_a_backfilled_state_does_not_charge_a_new_type_for_old_failures(self, orch):
        await fail(orch, FailureType.DEPLOYMENT_FAILURE)
        state = orch.get_deployment_state("d1")
        state.attempts = {}
        orch.save_deployment_state(state)

        result = await fail(orch, FailureType.BUILD_FAILURE)

        assert result["action"] == "retry"
        assert orch.get_deployment_state("d1").attempts == {"deployment_failure": 1, "build_failure": 1}

    def test_an_old_state_document_without_attempts_loads(self, orch):
        raw = json.dumps({
            "deployment_id": "d1", "namespace": "production", "app_name": "sample-app", "version": "v2",
            "status": "failed", "previous_version": "v1", "retry_count": 2,
            "failure_type": "deployment_failure", "timestamp": 1.0, "metadata": {},
        })

        state = orch._decode_state(raw)

        assert state.attempts == {} and state.retry_count == 2

    @pytest.mark.asyncio
    async def test_a_rerun_keeps_the_per_type_counts(self, orch):
        await fail(orch, FailureType.DEPLOYMENT_FAILURE)

        orch.record_deployment_started("d1", NS_NAME, "sample-app", "v2")

        assert orch.get_deployment_state("d1").attempts == {"deployment_failure": 1}


class TestPolicyInTheOrchestrator:
    def test_assigning_a_policy_replaces_the_source(self, orch):
        orch.policy = retry_policy(attempts=4)

        assert orch.policy.rule_for("production", "app", "deployment_failure").max_attempts == 4
        assert orch.policy_info()["file"] is None

    def test_a_policy_file_is_followed_while_the_orchestrator_runs(self, orch, tmp_path):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        path.write_text(json.dumps(DEFAULT_POLICY))
        now = [0.0]
        orch._policy_source = PolicySource(str(path), 5.0, orch._count_policy_reload, clock=lambda: now[0])
        before = counter("mana_policy_reloads_total", outcome="loaded")
        rejected = counter("mana_policy_reloads_total", outcome="rejected")

        edited = {**DEFAULT_POLICY, "default": {**DEFAULT_POLICY["default"], "health_check_failure": {"action": "alert"}}}
        path.write_text(json.dumps(edited) + " ")
        now[0] = 10
        assert orch.policy.rule_for("production", "app", "health_check_failure").action.value == "alert"

        path.write_text("default: [")
        now[0] = 20
        assert orch.policy.rule_for("production", "app", "health_check_failure").action.value == "alert"

        assert counter("mana_policy_reloads_total", outcome="loaded") == before + 1
        assert counter("mana_policy_reloads_total", outcome="rejected") == rejected + 1

    @pytest.mark.asyncio
    async def test_a_reloaded_policy_governs_the_next_failure(self, orch, tmp_path):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        path.write_text(json.dumps(DEFAULT_POLICY))
        now = [0.0]
        orch._policy_source = PolicySource(str(path), 5.0, clock=lambda: now[0])
        assert (await fail(orch, FailureType.HEALTH_CHECK_FAILURE, "d1"))["action"] == "rollback"

        edited = {**DEFAULT_POLICY, "default": {**DEFAULT_POLICY["default"], "health_check_failure": {"action": "alert"}}}
        path.write_text(json.dumps(edited) + " ")
        now[0] = 10

        assert (await fail(orch, FailureType.HEALTH_CHECK_FAILURE, "d2"))["action"] == "alert"

    def test_the_policy_info_names_the_file_and_the_reload_interval(self, orch, tmp_path):
        from src.policy import PolicySource

        path = tmp_path / "policy.yaml"
        path.write_text(json.dumps(DEFAULT_POLICY))
        orch._policy_source = PolicySource(str(path), 7.5)

        info = orch.policy_info()

        assert info["file"] == str(path) and info["reload_seconds"] == 7.5
        assert info["loaded_at"] == pytest.approx(time.time(), abs=5)
