"""Post-deploy verification as the orchestrator runs it: hold, evaluate, settle or fail."""

import logging
from unittest.mock import Mock, patch

import httpx
import pytest
from prometheus_client import REGISTRY

from src.jobs import Job, JobDeferred
from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator
from src.policy import DEFAULT_POLICY, Policy, PolicySource
from src.prometheus import PrometheusError
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis
from tests.test_api import webhook_body

NS = "production"
ERROR_CHECK = {"name": "error-rate", "query": 'e{namespace="{{namespace}}"}', "max": 0.05}
LATENCY_CHECK = {"name": "p95", "query": 'l{namespace="{{namespace}}"}', "max": 0.5}


def counter(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def verification_policy(**changes):
    settings = {"enabled": True, "checks": [ERROR_CHECK], **changes}
    return Policy.model_validate({**DEFAULT_POLICY, "verification": settings})


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
    instance.prometheus = Mock()
    instance.prometheus.query.return_value = 0.01
    instance.policy = verification_policy()
    return instance


def succeed(orch, deployment_id="d1", namespace=NS, version="v2", metadata=None):
    return orch.handle_deployment_success(deployment_id, namespace, "sample-app", version, metadata)


def verify_job(deployment_id="d1"):
    return Job(id="j", kind="verify", payload={"deployment_id": deployment_id}, attempts=1)


def run_round(orch, deployment_id="d1"):
    """One evaluation round; returns the deferral, or None when the job settled"""
    try:
        orch._run_verify_job(verify_job(deployment_id))
    except JobDeferred as deferral:
        return deferral
    return None


def age(orch, seconds, deployment_id="d1"):
    state = orch.get_deployment_state(deployment_id)
    state.verification["started_at"] -= seconds
    orch.save_deployment_state(state)


def last_successful(orch):
    return orch.redis_client.get(f"last_successful:{NS}:sample-app")


class TestHoldingASuccessfulDeployment:
    def test_where_verification_applies_the_deployment_is_held_not_recorded(self, orch):
        transition = succeed(orch, metadata={"commit": "abc"})

        state = orch.get_deployment_state("d1")
        assert transition.state.status is DeploymentStatus.VERIFYING
        assert state.status is DeploymentStatus.VERIFYING
        assert state.metadata["commit"] == "abc"
        assert last_successful(orch) is None

    def test_the_first_evaluation_is_queued_after_the_warmup(self, orch):
        orch.policy = verification_policy(warmup_seconds=45)

        succeed(orch)

        (job,) = orch.jobs.of_kind("verify")
        assert job["delay"] == 45
        assert job["payload"] == {"deployment_id": "d1"}
        assert job["job_id"].startswith("verify:d1:")

    def test_the_verification_progress_starts_empty(self, orch):
        succeed(orch)

        info = orch.get_deployment_state("d1").verification

        assert (info["evaluations"], info["streaks"], info["last"]) == (0, {}, {})

    def test_it_is_not_counted_as_an_outcome_while_it_is_held(self, orch):
        before = counter("deployments_total", namespace=NS, status="success")

        succeed(orch)

        assert counter("deployments_total", namespace=NS, status="success") == before
        assert orch.active_deployment_counts().get(NS) == 1

    @pytest.mark.parametrize("arrange", [
        lambda orch: setattr(orch, "prometheus", None),
        lambda orch: setattr(orch, "policy", Policy.model_validate(DEFAULT_POLICY)),
        lambda orch: setattr(orch, "policy", verification_policy(namespaces=["staging"])),
    ], ids=["no prometheus", "verification off", "other namespace"])
    def test_otherwise_success_is_recorded_at_once(self, orch, arrange):
        arrange(orch)

        transition = succeed(orch)

        assert transition.state.status is DeploymentStatus.SUCCESS
        assert last_successful(orch) == "v2"
        assert orch.jobs.of_kind("verify") == []

    def test_a_repeated_success_event_does_not_restart_or_settle_the_verification(self, orch):
        succeed(orch)
        first = orch.get_deployment_state("d1").verification["started_at"]

        again = succeed(orch)

        assert again.state.status is DeploymentStatus.VERIFYING
        assert orch.get_deployment_state("d1").verification["started_at"] == first
        assert len(orch.jobs.of_kind("verify")) == 1
        assert last_successful(orch) is None

    def test_a_deployment_that_was_already_started_keeps_its_history(self, orch):
        orch.record_deployment_started("d1", NS, "sample-app", "v2")
        started = orch.get_deployment_state("d1").started_at

        succeed(orch)

        state = orch.get_deployment_state("d1")
        assert state.status is DeploymentStatus.VERIFYING and state.started_at == started

    def test_a_deployment_that_already_succeeded_is_not_verified_again(self, orch):
        orch.policy = Policy.model_validate(DEFAULT_POLICY)
        succeed(orch)
        orch.policy = verification_policy()

        again = succeed(orch)

        assert again.state.status is DeploymentStatus.SUCCESS
        assert orch.jobs.of_kind("verify") == []

    def test_a_rerun_that_succeeds_after_failures_is_verified_too(self, orch):
        import asyncio

        asyncio.run(orch.handle_deployment_failure(
            "d1", NS, "sample-app", "v2", FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "9"}
        ))

        succeed(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING


class TestEvaluating:
    def start(self, orch, **policy):
        if policy:
            orch.policy = verification_policy(**policy)
        succeed(orch)

    def test_a_healthy_round_before_the_bake_time_defers_the_next(self, orch):
        orch.policy = verification_policy(interval_seconds=20)
        succeed(orch)

        deferral = run_round(orch)

        assert deferral.delay == 20
        info = orch.get_deployment_state("d1").verification
        assert info["evaluations"] == 1 and info["streaks"] == {"error-rate": 0}
        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING
        assert last_successful(orch) is None

    def test_the_query_is_rendered_for_the_deployment(self, orch):
        succeed(orch)

        run_round(orch)

        orch.prometheus.query.assert_called_once_with('e{namespace="production"}')

    def test_a_healthy_deployment_that_outlasts_the_bake_time_passes(self, orch, caplog):
        succeed(orch)
        age(orch, 1000)
        before = counter("mana_verifications_total", namespace=NS, outcome="passed")

        with caplog.at_level(logging.INFO):
            assert run_round(orch) is None

        state = orch.get_deployment_state("d1")
        assert state.status is DeploymentStatus.SUCCESS
        assert last_successful(orch) == "v2"
        assert counter("mana_verifications_total", namespace=NS, outcome="passed") == before + 1
        assert "AUDIT: verification_result" in caplog.text

    def test_a_passed_verification_is_counted_as_a_success_outcome_once(self, orch):
        succeed(orch)
        age(orch, 1000)
        before = counter("deployments_total", namespace=NS, status="success")

        run_round(orch)

        assert counter("deployments_total", namespace=NS, status="success") == before + 1

    def test_the_checks_are_counted_by_result(self, orch):
        orch.policy = verification_policy(checks=[ERROR_CHECK, LATENCY_CHECK])
        orch.prometheus.query.side_effect = lambda expression: 0.9 if expression.startswith("l") else 0.01
        succeed(orch)
        ok = counter("mana_verification_checks_total", check="error-rate", result="ok")
        breach = counter("mana_verification_checks_total", check="p95", result="breach")

        run_round(orch)

        assert counter("mana_verification_checks_total", check="error-rate", result="ok") == ok + 1
        assert counter("mana_verification_checks_total", check="p95", result="breach") == breach + 1

    def test_a_job_for_a_deployment_that_is_not_verifying_does_nothing(self, orch):
        orch.policy = Policy.model_validate(DEFAULT_POLICY)
        succeed(orch)

        assert run_round(orch) is None
        orch.prometheus.query.assert_not_called()

    def test_a_late_job_for_a_deployment_that_already_settled_does_not_run_again(self, orch):
        succeed(orch)
        age(orch, 1000)
        run_round(orch)
        orch.prometheus.query.reset_mock()
        passed = counter("mana_verifications_total", namespace=NS, outcome="passed")
        skipped = counter("mana_verifications_total", namespace=NS, outcome="skipped")

        assert run_round(orch) is None

        orch.prometheus.query.assert_not_called()
        assert counter("mana_verifications_total", namespace=NS, outcome="passed") == passed
        assert counter("mana_verifications_total", namespace=NS, outcome="skipped") == skipped
        assert orch.get_deployment_state("d1").status is DeploymentStatus.SUCCESS

    def test_a_job_for_an_unknown_deployment_does_nothing(self, orch):
        assert run_round(orch, "nope") is None
        orch.prometheus.query.assert_not_called()

    def test_a_newer_deployment_supersedes_the_verification(self, orch):
        succeed(orch)
        orch.get_live_version.return_value = "v3"
        before = counter("mana_verifications_total", namespace=NS, outcome="superseded")

        assert run_round(orch) is None

        assert orch.get_deployment_state("d1").status is DeploymentStatus.SUCCESS
        assert last_successful(orch) is None
        assert counter("mana_verifications_total", namespace=NS, outcome="superseded") == before + 1
        orch.prometheus.query.assert_not_called()

    def test_an_unreadable_live_version_does_not_stop_the_verification(self, orch):
        succeed(orch)
        orch.get_live_version.return_value = None

        assert run_round(orch).delay == orch.policy.verification.interval_seconds

    def test_turning_verification_off_while_one_is_running_settles_it_as_before(self, orch):
        succeed(orch)
        orch.policy = Policy.model_validate(DEFAULT_POLICY)

        assert run_round(orch) is None

        assert orch.get_deployment_state("d1").status is DeploymentStatus.SUCCESS
        assert last_successful(orch) == "v2"

    def test_losing_prometheus_while_one_is_running_settles_it_as_before(self, orch):
        succeed(orch)
        orch.prometheus = None

        assert run_round(orch) is None
        assert last_successful(orch) == "v2"


class TestFailingVerification:
    def breach(self, orch, rounds):
        orch.prometheus.query.return_value = 0.5
        for _ in range(rounds):
            run_round(orch)

    def test_breaches_in_a_row_fail_the_deployment_and_roll_it_back(self, orch, caplog):
        succeed(orch)
        before = counter("mana_verifications_total", namespace=NS, outcome="failed")

        with caplog.at_level(logging.INFO):
            self.breach(orch, 3)

        state = orch.get_deployment_state("d1")
        assert state.status is DeploymentStatus.ROLLING_BACK
        assert state.failure_type is FailureType.VERIFICATION_FAILURE
        assert "error-rate: 0.5 > 0.05" in state.metadata["verification"]
        (job,) = orch.jobs.of_kind("rollback")
        assert job["payload"]["target_version"] == "v1" and job["payload"]["version"] == "v2"
        assert counter("mana_verifications_total", namespace=NS, outcome="failed") == before + 1
        assert "AUDIT: verification_result" in caplog.text

    def test_the_failed_version_never_becomes_the_rollback_target(self, orch):
        succeed(orch)

        self.breach(orch, 3)

        assert last_successful(orch) is None

    def test_one_breach_less_than_the_threshold_does_not_fail_it(self, orch):
        succeed(orch)

        self.breach(orch, 2)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING
        assert orch.jobs.of_kind("rollback") == []

    def test_a_healthy_value_in_between_resets_the_streak(self, orch):
        succeed(orch)
        for value in (0.5, 0.5, 0.01, 0.5, 0.5):
            orch.prometheus.query.return_value = value
            run_round(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING

    def test_one_failing_check_is_enough(self, orch):
        orch.policy = verification_policy(checks=[ERROR_CHECK, LATENCY_CHECK])
        orch.prometheus.query.side_effect = lambda expression: 0.9 if expression.startswith("l") else 0.01
        succeed(orch)

        for _ in range(3):
            run_round(orch)

        state = orch.get_deployment_state("d1")
        assert state.status is DeploymentStatus.ROLLING_BACK
        assert "p95" in state.metadata["verification"] and "error-rate" not in state.metadata["verification"]

    def test_no_data_does_not_fail_it_by_default(self, orch):
        succeed(orch)
        orch.prometheus.query.return_value = None

        for _ in range(5):
            run_round(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING

    def test_no_data_fails_it_when_the_policy_says_it_counts(self, orch):
        orch.policy = verification_policy(on_missing="breach")
        succeed(orch)
        orch.prometheus.query.return_value = None

        for _ in range(3):
            run_round(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.ROLLING_BACK

    def test_prometheus_errors_do_not_fail_it_by_default_but_are_counted(self, orch):
        succeed(orch)
        orch.prometheus.query.side_effect = PrometheusError("cannot reach Prometheus")
        before = counter("mana_verification_checks_total", check="error-rate", result="error")

        for _ in range(5):
            run_round(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.VERIFYING
        assert counter("mana_verification_checks_total", check="error-rate", result="error") == before + 5

    def test_prometheus_errors_fail_it_when_the_policy_says_they_count(self, orch):
        orch.policy = verification_policy(on_missing="breach")
        succeed(orch)
        orch.prometheus.query.side_effect = PrometheusError("down")

        for _ in range(3):
            run_round(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.ROLLING_BACK

    def test_the_policy_decides_what_a_verification_failure_leads_to(self, orch):
        default = {**DEFAULT_POLICY["default"], "verification_failure": {"action": "alert"}}
        orch.policy = Policy.model_validate({**DEFAULT_POLICY, "default": default,
                                             "verification": {"enabled": True, "checks": [ERROR_CHECK]}})
        succeed(orch)

        self.breach(orch, 3)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.FAILED
        assert orch.jobs.of_kind("rollback") == []

    def test_a_breach_with_no_known_earlier_version_alerts_instead_of_rolling_back(self, orch):
        orch.get_previous_version.return_value = None
        succeed(orch)

        self.breach(orch, 3)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.FAILED

    def test_the_circuit_breaker_still_applies_to_a_verification_failure(self, orch):
        orch.policy = Policy.model_validate({**DEFAULT_POLICY, "circuit_breaker": {"max_rollbacks": 1},
                                             "verification": {"enabled": True, "checks": [ERROR_CHECK]}})
        orch.guards.record_rollback(NS, "sample-app", "earlier", 3600)
        succeed(orch)

        self.breach(orch, 3)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.FAILED


class TestAbandonedVerification:
    def test_a_verification_that_cannot_run_records_the_version_unverified_and_warns(self, orch):
        succeed(orch)
        orch.alerter.dispatch = Mock()
        before = counter("mana_verifications_total", namespace=NS, outcome="error")

        orch.handle_dead_job(verify_job(), RuntimeError("redis exploded"))

        assert orch.get_deployment_state("d1").status is DeploymentStatus.SUCCESS
        assert last_successful(orch) == "v2"
        assert counter("mana_verifications_total", namespace=NS, outcome="error") == before + 1
        alert = orch.alerter.dispatch.call_args.args[0]
        assert alert.event == "verification_abandoned" and alert.severity == "warning"

    def test_a_dead_verify_job_for_a_deployment_that_moved_on_changes_nothing(self, orch):
        succeed(orch)
        age(orch, 1000)
        run_round(orch)

        orch.handle_dead_job(verify_job(), RuntimeError("late"))

        assert orch.get_deployment_state("d1").status is DeploymentStatus.SUCCESS


class TestConfigAndInfo:
    def test_the_policy_info_says_whether_verification_is_active(self, orch):
        assert orch.policy_info()["verification_active"] is True

        orch.prometheus = None
        assert orch.policy_info()["verification_active"] is False

        orch.prometheus = Mock()
        orch.policy = Policy.model_validate(DEFAULT_POLICY)
        assert orch.policy_info()["verification_active"] is False


@pytest.fixture
def served(api):
    transport = httpx.ASGITransport(app=api.app)
    with patch.object(api.orchestrator, "redis_client", FakeRedis()), \
         patch.object(api.orchestrator, "jobs", FakeJobQueue()), \
         patch.object(api.orchestrator, "prometheus", Mock()), \
         patch.object(api.orchestrator, "_policy_source", PolicySource(policy=verification_policy(bake_seconds=240))):
        yield api, httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_the_success_webhook_answers_verifying_with_the_bake_time(served):
    api, client = served

    async with client:
        response = await client.post("/webhook/deployment", json=webhook_body("success", {}))
        state = await client.get("/deployment/deploy-first-failure")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "verifying" and body["bake_seconds"] == 240
    assert "240s" in body["message"]
    assert state.json()["status"] == "verifying"


@pytest.mark.asyncio
async def test_the_success_webhook_still_answers_success_without_verification(served):
    api, client = served
    api.orchestrator.policy = Policy.model_validate(DEFAULT_POLICY)

    async with client:
        response = await client.post("/webhook/deployment", json=webhook_body("success", {}))

    assert response.json()["status"] == "success"
    assert "rollbacks" in response.json()["message"]


@pytest.mark.asyncio
async def test_a_replayed_success_webhook_keeps_answering_verifying(served):
    api, client = served

    async with client:
        first = await client.post("/webhook/deployment", json=webhook_body("success", {}))
        second = await client.post("/webhook/deployment", json={**webhook_body("success", {}), "metadata": {"n": "2"}})

    assert (first.json()["status"], second.json()["status"]) == ("verifying", "verifying")
    assert len(api.orchestrator.jobs.of_kind("verify")) == 1
