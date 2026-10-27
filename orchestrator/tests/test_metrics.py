from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import redis
from prometheus_client import REGISTRY, generate_latest

from src import metrics
from src.metrics import ActiveDeploymentsCollector, CountingRedis
from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator
from tests.fakes import FakeRedis
from tests.test_api import webhook_body


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Delta:
    """Change of a sample since this object was created"""

    def __init__(self, name, **labels):
        self.name, self.labels = name, labels
        self.before = sample(name, **labels)

    @property
    def value(self):
        return sample(self.name, **self.labels) - self.before


@pytest.fixture
def orchestrator():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        orch = SelfHealingOrchestrator(use_config=False)
    orch.redis_client = FakeRedis()
    orch.max_retries = 1
    orch.get_previous_version = Mock(return_value="v0")
    orch.trigger_github_workflow_rerun = AsyncMock()
    return orch


NS = "metrics-ns"


def started(orch, deployment_id="d1"):
    return orch.record_deployment_started(deployment_id, NS, "sample-app", "v1")


async def fail(orch, deployment_id="d1", failure=FailureType.DEPLOYMENT_FAILURE, run_id="9"):
    return await orch.handle_deployment_failure(
        deployment_id, NS, "sample-app", "v1", failure, {"workflow_run_id": run_id} if run_id else {}
    )


class TestOutcomes:
    def test_success_is_counted_once_with_its_duration(self, orchestrator):
        count = Delta("deployments_total", namespace=NS, status="success")
        durations = Delta("deployment_duration_seconds_count", namespace=NS, status="success")
        started(orchestrator)

        for _ in range(2):
            orchestrator.record_deployment_succeeded("d1", NS, "sample-app", "v1")

        assert count.value == 1
        assert durations.value == 1

    def test_in_progress_is_not_counted(self, orchestrator):
        count = Delta("deployments_total", namespace=NS, status="in_progress")

        started(orchestrator)

        assert count.value == 0

    @pytest.mark.asyncio
    async def test_retry_is_not_an_outcome(self, orchestrator):
        outcomes = [Delta("deployments_total", namespace=NS, status=s) for s in ("failed", "success", "rolled_back")]
        started(orchestrator)

        result = await fail(orchestrator)

        assert result["action"] == "retry"
        assert [o.value for o in outcomes] == [0, 0, 0]

    @pytest.mark.asyncio
    async def test_alert_counts_failed_once_even_if_repeated(self, orchestrator):
        failed = Delta("deployments_total", namespace=NS, status="failed")
        started(orchestrator)

        await fail(orchestrator, run_id=None)
        await fail(orchestrator, run_id=None)

        assert failed.value == 1

    @pytest.mark.asyncio
    async def test_a_rerun_that_settles_again_is_counted_again(self, orchestrator):
        failed = Delta("deployments_total", namespace=NS, status="failed")
        success = Delta("deployments_total", namespace=NS, status="success")
        started(orchestrator)
        await fail(orchestrator, run_id=None)

        started(orchestrator)
        orchestrator.record_deployment_succeeded("d1", NS, "sample-app", "v1")

        assert (failed.value, success.value) == (1, 1)

    @pytest.mark.asyncio
    async def test_rollback_counts_rolled_back_and_recovery_time(self, orchestrator):
        rolled_back = Delta("deployments_total", namespace=NS, status="rolled_back")
        recovery = Delta(
            "mana_recovery_duration_seconds_count", namespace=NS, app="sample-app", outcome="rolled_back"
        )
        orchestrator.get_live_version = Mock(return_value="v1")
        orchestrator.rollback_deployment = Mock(return_value=True)
        started(orchestrator)
        await fail(orchestrator)

        result = await fail(orchestrator)

        assert result["action"] == "rollback"
        assert rolled_back.value == 1
        assert recovery.value == 1

    @pytest.mark.asyncio
    async def test_success_after_retry_records_recovery_time(self, orchestrator):
        recovery = Delta(
            "mana_recovery_duration_seconds_count", namespace=NS, app="sample-app", outcome="retry_succeeded"
        )
        started(orchestrator)
        await fail(orchestrator)

        orchestrator.record_deployment_succeeded("d1", NS, "sample-app", "v1")

        assert recovery.value == 1

    def test_success_without_a_failure_has_no_recovery_time(self, orchestrator):
        recovery = Delta(
            "mana_recovery_duration_seconds_count", namespace=NS, app="sample-app", outcome="retry_succeeded"
        )
        started(orchestrator)

        orchestrator.record_deployment_succeeded("d1", NS, "sample-app", "v1")

        assert recovery.value == 0

    def test_unreadable_previous_document_counts_as_new(self, orchestrator):
        success = Delta("deployments_total", namespace=NS, status="success")
        orchestrator.redis_client.set("deployment:d1", "not json")

        orchestrator.record_deployment_succeeded("d1", NS, "sample-app", "v1")

        assert success.value == 1


class TestDecisionLatency:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", ["retry", "no_run_id", "build_failure", "rollback", "skip", "no_previous"])
    async def test_every_decision_is_timed(self, orchestrator, scenario):
        observed = Delta("rollback_decision_duration_seconds_count")
        orchestrator.get_live_version = Mock(return_value="v1")
        orchestrator.rollback_deployment = Mock(return_value=True)
        started(orchestrator)
        if scenario in ("rollback", "skip", "no_previous", "build_failure"):
            await fail(orchestrator)
        if scenario == "skip":
            orchestrator.get_live_version = Mock(return_value="other")
        if scenario == "no_previous":
            orchestrator.get_previous_version = Mock(return_value=None)
            state = orchestrator.get_deployment_state("d1")
            state.previous_version = None
            orchestrator.save_deployment_state(state)
        before = observed.value

        failure = FailureType.BUILD_FAILURE if scenario == "build_failure" else FailureType.DEPLOYMENT_FAILURE
        await fail(orchestrator, failure=failure, run_id=None if scenario == "no_run_id" else "9")

        assert observed.value - before == 1


class TestHealthCheckMetrics:
    @pytest.mark.parametrize("healthy,result", [(True, "success"), (False, "failed")])
    def test_result_and_duration_are_recorded(self, orchestrator, healthy, result):
        total = Delta("health_checks_total", namespace=NS, result=result)
        durations = Delta("health_check_duration_seconds_count", namespace=NS, deployment="sample-app")
        orchestrator._wait_for_healthy_deployment = Mock(return_value=healthy)

        assert orchestrator.check_deployment_health(NS, "sample-app") is healthy

        assert total.value == 1
        assert durations.value == 1

    def test_an_exception_counts_as_failed_and_propagates(self, orchestrator):
        total = Delta("health_checks_total", namespace=NS, result="failed")
        orchestrator._wait_for_healthy_deployment = Mock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            orchestrator.check_deployment_health(NS, "sample-app")

        assert total.value == 1


class TestCountingRedis:
    def test_passes_results_through(self):
        client = Mock()
        client.get.return_value = "value"
        client.host = "redis"

        wrapped = CountingRedis(client)

        assert wrapped.get("k") == "value"
        assert wrapped.host == "redis"

    @pytest.mark.parametrize("error", [redis.ConnectionError("x"), redis.TimeoutError("x")])
    def test_connection_and_timeout_errors_are_counted_and_raised(self, error):
        errors = Delta("redis_connection_errors_total")
        client = Mock()
        client.get.side_effect = error

        with pytest.raises(type(error)):
            CountingRedis(client).get("k")

        assert errors.value == 1

    def test_other_errors_are_not_counted(self):
        errors = Delta("redis_connection_errors_total")
        client = Mock()
        client.get.side_effect = redis.ResponseError("syntax")

        with pytest.raises(redis.ResponseError):
            CountingRedis(client).get("k")

        assert errors.value == 0


class TestActiveDeploymentsCollector:
    def values(self, collector):
        return {s.labels["namespace"]: s.value for f in collector.collect() for s in f.samples}

    def test_reports_counts_per_namespace(self):
        collector = ActiveDeploymentsCollector(lambda: {"a": 2, "b": 1})

        assert self.values(collector) == {"a": 2, "b": 1}

    def test_namespaces_seen_before_fall_to_zero(self):
        counts = {"a": 2}
        collector = ActiveDeploymentsCollector(lambda: counts)
        self.values(collector)
        counts.clear()

        assert self.values(collector) == {"a": 0}

    def test_redis_failure_yields_nothing(self):
        def broken():
            raise redis.ConnectionError("down")

        assert list(ActiveDeploymentsCollector(broken).collect()) == []


class TestWebhookMetrics:
    async def post(self, api, body):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api.orchestrator, "redis_client", FakeRedis()):
                return await client.post("/webhook/deployment", json=body)

    @pytest.mark.asyncio
    async def test_successful_request_is_counted(self, api):
        requests = Delta("webhook_requests_total", endpoint="/webhook/deployment", code="200")
        errors = Delta("webhook_errors_total", endpoint="/webhook/deployment", reason="validation")

        response = await self.post(api, webhook_body("in_progress", {}))

        assert response.status_code == 200
        assert requests.value == 1
        assert errors.value == 0

    @pytest.mark.asyncio
    async def test_validation_error_is_counted(self, api):
        requests = Delta("webhook_requests_total", endpoint="/webhook/deployment", code="422")
        errors = Delta("webhook_errors_total", endpoint="/webhook/deployment", reason="validation")

        response = await self.post(api, {**webhook_body("in_progress", {}), "status": "bogus"})

        assert response.status_code == 422
        assert requests.value == 1
        assert errors.value == 1

    @pytest.mark.asyncio
    async def test_server_error_is_counted(self, api):
        errors = Delta("webhook_errors_total", endpoint="/webhook/deployment", reason="server_error")

        with patch.object(api.orchestrator, "record_deployment_succeeded", side_effect=RuntimeError("x")):
            response = await self.post(api, webhook_body("success", {}))

        assert response.status_code == 500
        assert errors.value == 1

    @pytest.mark.asyncio
    async def test_other_endpoints_are_not_counted(self, api):
        before = sum(
            s.value for f in REGISTRY.collect() if f.name == "webhook_requests" for s in f.samples
            if s.name == "webhook_requests_total"
        )
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/config")

        after = sum(
            s.value for f in REGISTRY.collect() if f.name == "webhook_requests" for s in f.samples
            if s.name == "webhook_requests_total"
        )
        assert after == before


def test_every_metric_the_rules_depend_on_is_registered(api):
    exposed = generate_latest().decode()

    for name in (
        "deployments_total", "rollbacks_total", "retries_total",
        "deployment_duration_seconds", "mana_recovery_duration_seconds",
        "rollback_decision_duration_seconds", "health_check_duration_seconds",
        "health_checks_total", "webhook_requests_total", "webhook_errors_total",
        "redis_connection_errors_total",
    ):
        assert f"# TYPE {name} " in exposed, name


@pytest.mark.asyncio
async def test_active_deployments_is_scraped_from_shared_state(api):
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(api.orchestrator, "redis_client", FakeRedis()):
            await client.post("/webhook/deployment", json=webhook_body("in_progress", {}))
            exposed = generate_latest().decode()

    assert 'active_deployments{namespace="production"} 1.0' in exposed
