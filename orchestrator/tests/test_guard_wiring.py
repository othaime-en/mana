"""Deny-list and circuit breaker as the recovery path uses them"""

import logging
from unittest.mock import Mock, patch

import httpx
import pytest
import redis
from prometheus_client import REGISTRY

from src.guards import Guards
from src.jobs import Job
from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator
from src.policy import DEFAULT_POLICY, Policy
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis
from tests.test_api import webhook_body


def counter(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


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


def policy(**sections):
    return Policy.model_validate({**DEFAULT_POLICY, **sections})


def settle_rollback(orch, deployment_id="d1", version="v2", namespace="production", ok=True):
    """Fail a deployment, then run the rollback job it queued"""
    orch.get_live_version.return_value = version
    import asyncio

    asyncio.run(orch.handle_deployment_failure(
        deployment_id, namespace, "sample-app", version, FailureType.HEALTH_CHECK_FAILURE, {"workflow_run_id": "1"}
    ))
    (job,) = [Job(id=j["job_id"], kind=j["kind"], payload=j["payload"], attempts=1)
              for j in orch.jobs.of_kind("rollback") if j["payload"]["deployment_id"] == deployment_id]
    with patch.object(orch, "rollback_deployment", return_value=ok):
        orch._run_rollback_job(job)


class TestFreezeAfterRollback:
    def test_the_failed_version_is_denied_and_the_rollback_counted(self, orch, caplog):
        before = counter("mana_version_denials_total", namespace="production", outcome="frozen")

        with caplog.at_level(logging.INFO):
            settle_rollback(orch)

        entry = orch.guards.denied("production", "sample-app", "v2")
        assert entry["reason"] == "rolled back after health_check_failure"
        assert entry["deployment_id"] == "d1"
        assert orch.guards.breaker("production", "sample-app", 3, 3600).rollbacks == 1
        assert counter("mana_version_denials_total", namespace="production", outcome="frozen") == before + 1
        assert "AUDIT: version_denied" in caplog.text

    def test_the_denial_lasts_as_long_as_the_policy_says(self, orch):
        orch.policy = policy(rollback={"deny_ttl_seconds": 120})

        settle_rollback(orch)

        entry = orch.guards.denied("production", "sample-app", "v2")
        assert entry["expires_at"] - entry["since"] == pytest.approx(120)

    def test_freezing_can_be_switched_off_but_the_rollback_still_counts(self, orch):
        orch.policy = policy(rollback={"freeze_failed_version": False})

        settle_rollback(orch)

        assert orch.guards.denied("production", "sample-app", "v2") is None
        assert orch.guards.breaker("production", "sample-app", 3, 3600).rollbacks == 1

    def test_a_failed_rollback_freezes_nothing(self, orch):
        settle_rollback(orch, ok=False)

        assert orch.guards.denied("production", "sample-app", "v2") is None
        assert orch.guards.breaker("production", "sample-app", 3, 3600).rollbacks == 0

    def test_trouble_recording_the_guards_does_not_undo_the_rollback(self, orch):
        with patch.object(Guards, "record_rollback", side_effect=redis.ConnectionError("down")):
            settle_rollback(orch)

        assert orch.get_deployment_state("d1").status is DeploymentStatus.ROLLED_BACK


class TestCircuitBreaker:
    async def fail(self, orch, deployment_id, version="v2"):
        orch.get_live_version.return_value = version
        return await orch.handle_deployment_failure(
            deployment_id, "production", "sample-app", version,
            FailureType.HEALTH_CHECK_FAILURE, {"workflow_run_id": "1"}
        )

    @pytest.mark.asyncio
    async def test_rollbacks_continue_below_the_limit(self, orch):
        orch.policy = policy(circuit_breaker={"max_rollbacks": 2})
        orch.guards.record_rollback("production", "sample-app", "older", 3600)

        result = await self.fail(orch, "d1")

        assert result["action"] == "rollback"

    @pytest.mark.asyncio
    async def test_automatic_rollback_pauses_at_the_limit(self, orch, caplog):
        orch.policy = policy(circuit_breaker={"max_rollbacks": 2})
        for name in ("a", "b"):
            orch.guards.record_rollback("production", "sample-app", name, 3600)
        before = counter("mana_circuit_breaker_trips_total", namespace="production")

        with caplog.at_level(logging.INFO):
            result = await self.fail(orch, "d1")

        assert result["action"] == "alert"
        assert "Circuit breaker open" in result["message"]
        assert "2 times" in result["message"]
        assert orch.jobs.of_kind("rollback") == []
        assert orch.get_deployment_state("d1").status is DeploymentStatus.FAILED
        assert counter("mana_circuit_breaker_trips_total", namespace="production") == before + 1
        assert "AUDIT: circuit_breaker_open" in caplog.text

    @pytest.mark.asyncio
    async def test_the_breaker_only_pauses_rollbacks_not_retries(self, orch):
        orch.policy = policy(circuit_breaker={"max_rollbacks": 1})
        orch.guards.record_rollback("production", "sample-app", "a", 3600)

        result = await orch.handle_deployment_failure(
            "d1", "production", "sample-app", "v2", FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "1"}
        )

        assert result["action"] == "retry"

    @pytest.mark.asyncio
    async def test_another_application_is_not_paused(self, orch):
        orch.policy = policy(circuit_breaker={"max_rollbacks": 1})
        orch.guards.record_rollback("production", "other-app", "a", 3600)

        assert (await self.fail(orch, "d1"))["action"] == "rollback"

    @pytest.mark.asyncio
    async def test_the_alert_names_the_pause_to_a_human(self, orch):
        orch.policy = policy(circuit_breaker={"max_rollbacks": 1})
        orch.guards.record_rollback("production", "sample-app", "a", 3600)
        orch.alerter.dispatch = Mock()

        await self.fail(orch, "d1")

        alert = orch.alerter.dispatch.call_args.args[0]
        assert alert.event == "manual_intervention" and alert.severity == "critical"
        assert "Circuit breaker open" in alert.message


class TestRefusingADeniedVersion:
    def test_an_allowed_version_passes(self, orch):
        assert orch.check_version_denied("d1", "production", "sample-app", "v2") is None

    def test_a_denied_version_is_refused_counted_audited_and_alerted(self, orch, caplog):
        orch.guards.deny("production", "sample-app", "v2", 60, "rolled back after timeout", "old")
        orch.alerter.dispatch = Mock()
        before = counter("mana_version_denials_total", namespace="production", outcome="refused")

        with caplog.at_level(logging.INFO):
            entry = orch.check_version_denied("d2", "production", "sample-app", "v2", {"commit": "abc"})

        assert entry["reason"] == "rolled back after timeout"
        assert counter("mana_version_denials_total", namespace="production", outcome="refused") == before + 1
        assert "AUDIT: version_denied" in caplog.text
        alert = orch.alerter.dispatch.call_args.args[0]
        assert (alert.event, alert.severity, alert.commit) == ("version_denied", "warning", "abc")

    def test_the_refused_deployment_is_not_persisted(self, orch):
        orch.guards.deny("production", "sample-app", "v2", 60, "why", "old")

        orch.check_version_denied("d2", "production", "sample-app", "v2")

        assert orch.get_deployment_state("d2") is None
        assert orch.active_deployment_counts() == {}


class TestAutomationStatus:
    def test_it_reports_the_breaker_and_the_denied_versions(self, orch):
        orch.guards.deny("production", "sample-app", "v2", 60, "why", "d1")
        orch.guards.record_rollback("production", "sample-app", "d1", 3600)

        status = orch.get_automation_status("production", "sample-app")

        assert status["circuit_breaker"] == {
            "open": False, "rollbacks_in_window": 1, "max_rollbacks": 3, "window_seconds": 3600
        }
        assert [entry["version"] for entry in status["denied_versions"]] == ["v2"]


@pytest.fixture
def served(api):
    transport = httpx.ASGITransport(app=api.app)
    with patch.object(api.orchestrator, "redis_client", FakeRedis()), \
         patch.object(api.orchestrator, "jobs", FakeJobQueue()):
        yield api, httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_a_denied_version_is_refused_when_its_deployment_starts(served):
    api, client = served
    api.orchestrator.guards.deny("production", "sample-app", "1.0.0", 60, "rolled back after timeout", "old")

    async with client:
        response = await client.post("/webhook/deployment", json=webhook_body("in_progress", {}))
        state = await client.get("/deployment/deploy-first-failure")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "denied"
    assert body["reason"] == "rolled back after timeout"
    assert "deny-list" in body["message"]
    assert state.status_code == 404


@pytest.mark.asyncio
async def test_an_allowed_version_still_starts_normally(served):
    api, client = served

    async with client:
        response = await client.post("/webhook/deployment", json=webhook_body("in_progress", {}))
        state = await client.get("/deployment/deploy-first-failure")

    assert response.json()["status"] == "acknowledged"
    assert state.status_code == 200


@pytest.mark.asyncio
async def test_other_statuses_of_a_denied_version_are_not_blocked(served):
    api, client = served
    api.orchestrator.guards.deny("production", "sample-app", "1.0.0", 60, "why", "old")

    async with client:
        response = await client.post(
            "/webhook/deployment", json=webhook_body("failed", {"workflow_run_id": "1"}, "deployment_failure")
        )

    assert response.json()["status"] == "processed"


@pytest.mark.asyncio
async def test_an_operator_can_lift_a_denial(served):
    api, client = served
    api.orchestrator.guards.deny("production", "sample-app", "v2", 60, "why", "old")

    async with client:
        lifted = await client.delete("/denied-versions/production/sample-app/v2")
        again = await client.delete("/denied-versions/production/sample-app/v2")

    assert lifted.status_code == 200
    assert lifted.json()["status"] == "allowed"
    assert again.status_code == 404
    assert api.orchestrator.guards.denied("production", "sample-app", "v2") is None


@pytest.mark.asyncio
async def test_lifting_a_denial_reports_a_redis_outage(served):
    api, client = served

    async with client:
        with patch.object(Guards, "allow", side_effect=redis.ConnectionError("down")):
            response = await client.delete("/denied-versions/production/sample-app/v2")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_the_status_endpoint_includes_the_guards(served):
    api, client = served
    api.orchestrator.guards.deny("production", "sample-app", "v2", 60, "why", "d1")

    async with client:
        with patch.object(api.orchestrator, "get_deployment_metrics", return_value={"replicas": 3}):
            response = await client.get("/deployments/production/sample-app/status")

    body = response.json()
    assert body["replicas"] == 3
    assert body["automation"]["circuit_breaker"]["open"] is False
    assert [entry["version"] for entry in body["automation"]["denied_versions"]] == ["v2"]
