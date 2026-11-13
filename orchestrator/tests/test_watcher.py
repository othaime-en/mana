import asyncio
import datetime as dt
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest
import redis
from prometheus_client import REGISTRY

from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator
from src.watcher import (
    DEADLINE_EXCEEDED,
    MANAGED_SELECTOR,
    NO_PROGRESS,
    StuckRollout,
    Watcher,
    assess_rollout,
    stuck_rollout,
)
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis

NOW = 1_800_000_000.0


def at(seconds_ago):
    return dt.datetime.fromtimestamp(NOW - seconds_ago, tz=dt.timezone.utc)


def progressing(reason="ReplicaSetUpdated", status="True", idle=300, message=""):
    return NS(type="Progressing", status=status, reason=reason, message=message, last_update_time=at(idle))


def deployment(
    name="sample-app", generation=2, replicas=3, observed=2, updated=3, total=3, ready=3,
    image="registry/sample-app:v2", conditions=(), containers=None,
):
    return NS(
        metadata=NS(name=name, generation=generation),
        spec=NS(replicas=replicas, template=NS(spec=NS(containers=containers or [NS(name=name, image=image)]))),
        status=NS(
            observed_generation=observed, updated_replicas=updated, replicas=total,
            ready_replicas=ready, conditions=list(conditions),
        ),
    )


def counter(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestAssessRollout:
    def test_a_progress_deadline_that_passed_is_stuck(self):
        exceeded = progressing("ProgressDeadlineExceeded", status="False", message="timed out")

        result = assess_rollout(deployment(conditions=[exceeded]), NOW, 120)

        assert result == (DEADLINE_EXCEEDED, "timed out")

    def test_the_deadline_reason_counts_only_when_progress_is_false(self):
        odd = progressing("ProgressDeadlineExceeded", status="True")

        assert assess_rollout(deployment(conditions=[odd]), NOW, 120) is None

    def test_a_rollout_with_no_progress_for_long_enough_is_stuck(self):
        stalled = deployment(ready=1, updated=1, conditions=[progressing(idle=200)])

        reason, detail = assess_rollout(stalled, NOW, 120)

        assert reason == NO_PROGRESS
        assert "1/3 replicas ready" in detail and "200 s" in detail

    def test_a_rollout_that_is_still_making_progress_is_left_alone(self):
        young = deployment(ready=1, updated=1, conditions=[progressing(idle=30)])

        assert assess_rollout(young, NOW, 120) is None

    @pytest.mark.parametrize("fields", [
        {"observed": 1},
        {"updated": 2},
        {"total": 4},
        {"ready": 2},
    ])
    def test_each_sign_of_an_unfinished_rollout_counts(self, fields):
        unfinished = deployment(conditions=[progressing()], **fields)

        assert assess_rollout(unfinished, NOW, 120)[0] == NO_PROGRESS

    def test_a_complete_rollout_is_never_stuck(self):
        done = deployment(conditions=[progressing()])

        assert assess_rollout(done, NOW, 120) is None

    def test_a_finished_rollout_that_lost_a_pod_is_degraded_not_stuck(self):
        degraded = deployment(ready=1, conditions=[progressing("NewReplicaSetAvailable", idle=9999)])

        assert assess_rollout(degraded, NOW, 120) is None

    def test_without_a_progressing_condition_nothing_is_assumed(self):
        assert assess_rollout(deployment(ready=0), NOW, 120) is None

    def test_a_progressing_condition_that_is_false_for_another_reason_is_ignored(self):
        odd = progressing("SomethingElse", status="False")

        assert assess_rollout(deployment(ready=0, conditions=[odd]), NOW, 120) is None

    def test_a_missing_update_time_is_not_guessed_at(self):
        undated = progressing()
        undated.last_update_time = None

        assert assess_rollout(deployment(ready=0, conditions=[undated]), NOW, 120) is None

    def test_a_deployment_without_status_is_tolerated(self):
        bare = deployment()
        bare.status = None

        assert assess_rollout(bare, NOW, 120) is None

    def test_a_missing_replica_count_means_one(self):
        single = deployment(replicas=None, updated=1, total=1, ready=1, conditions=[progressing()])
        waiting = deployment(replicas=None, updated=1, total=1, ready=0, conditions=[progressing()])

        assert assess_rollout(single, NOW, 120) is None
        assert assess_rollout(waiting, NOW, 120)[0] == NO_PROGRESS

    def test_the_idle_threshold_is_inclusive(self):
        stalled = deployment(ready=0, conditions=[progressing(idle=120)])

        assert assess_rollout(stalled, NOW, 120) is not None
        assert assess_rollout(stalled, NOW, 121) is None


class TestStuckRollout:
    stalled = dict(ready=0, conditions=[progressing()])

    def test_the_version_is_the_live_image_tag(self):
        result = stuck_rollout(deployment(**self.stalled), "production", NOW, 120)

        assert (result.app, result.version, result.generation) == ("sample-app", "v2", 2)
        assert result.deployment_id == "watch-production-sample-app-g2"

    def test_a_healthy_deployment_gives_nothing(self):
        assert stuck_rollout(deployment(), "production", NOW, 120) is None

    DIGEST = "sha256:" + "ab12cd34ef56" + "0" * 52

    def test_a_digest_pinned_image_is_named_by_a_short_digest(self):
        pinned = deployment(image=f"registry/app@{self.DIGEST}", **self.stalled)

        assert stuck_rollout(pinned, "production", NOW, 120).version == "sha256:ab12cd34ef56"

    def test_an_untagged_image_is_named_latest(self):
        untagged = deployment(image="registry/app", **self.stalled)

        assert stuck_rollout(untagged, "production", NOW, 120).version == "latest"

    def test_a_tag_wins_over_a_digest(self):
        both = deployment(image=f"registry/app:v2@{self.DIGEST}", **self.stalled)

        assert stuck_rollout(both, "production", NOW, 120).version == "v2"

    def test_an_ambiguous_container_cannot_be_tracked(self):
        containers = [NS(name="a", image="r/a:v1"), NS(name="b", image="r/b:v1")]

        assert stuck_rollout(deployment(containers=containers, **self.stalled), "production", NOW, 120) is None

    def test_a_new_generation_is_a_new_rollout(self):
        first = stuck_rollout(deployment(generation=2, **self.stalled), "production", NOW, 120)
        second = stuck_rollout(deployment(generation=3, **self.stalled), "production", NOW, 120)

        assert first.deployment_id != second.deployment_id


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


def watcher_for(orch, namespaces=("production",), interval=0.01, stuck=120):
    return Watcher(orch, list(namespaces), interval, stuck)


def rollout(version="v2", generation=2, namespace="production"):
    return StuckRollout(namespace, "sample-app", version, generation, NO_PROGRESS, "0/3 replicas ready")


class TestFindStuck:
    def test_it_lists_only_managed_deployments_in_each_namespace(self, orch):
        stalled = deployment(ready=0, conditions=[progressing(idle=100000)])
        orch.k8s_apps.list_namespaced_deployment.side_effect = lambda namespace, label_selector: NS(
            items=[stalled] if namespace == "production" else [deployment()]
        )

        with patch("src.watcher.time.time", return_value=NOW):
            found = watcher_for(orch, ("production", "staging")).find_stuck()

        assert [(s.namespace, s.app) for s in found] == [("production", "sample-app")]
        for call in orch.k8s_apps.list_namespaced_deployment.call_args_list:
            assert call.kwargs["label_selector"] == MANAGED_SELECTOR == "mana.io/managed=true"

    def test_one_namespace_failing_does_not_stop_the_others(self, orch):
        stalled = deployment(ready=0, conditions=[progressing(idle=100000)])

        def listing(namespace, label_selector):
            if namespace == "broken":
                raise RuntimeError("403")
            return NS(items=[stalled])

        orch.k8s_apps.list_namespaced_deployment.side_effect = listing
        errors = counter("mana_watcher_scans_total", outcome="error")
        oks = counter("mana_watcher_scans_total", outcome="ok")

        with patch("src.watcher.time.time", return_value=NOW):
            found = watcher_for(orch, ("broken", "production")).find_stuck()

        assert [s.namespace for s in found] == ["production"]
        assert counter("mana_watcher_scans_total", outcome="error") == errors + 1
        assert counter("mana_watcher_scans_total", outcome="ok") == oks + 1


class TestHandle:
    @pytest.mark.asyncio
    async def test_a_stuck_rollout_goes_through_the_policy_and_rolls_back(self, orch):
        before = counter("mana_watcher_detections_total", namespace="production", reason=NO_PROGRESS)

        handled = await watcher_for(orch).handle(rollout())

        assert handled is True
        state = orch.get_deployment_state("watch-production-sample-app-g2")
        assert state.status is DeploymentStatus.ROLLING_BACK
        assert state.failure_type is FailureType.ROLLOUT_STUCK
        assert state.metadata["detected_by"] == "watcher"
        assert state.metadata["generation"] == "2"
        (job,) = orch.jobs.of_kind("rollback")
        assert job["payload"]["target_version"] == "v1"
        assert counter("mana_watcher_detections_total", namespace="production", reason=NO_PROGRESS) == before + 1

    @pytest.mark.asyncio
    async def test_the_same_rollout_is_handled_once(self, orch):
        watcher = watcher_for(orch)

        first = await watcher.handle(rollout())
        second = await watcher.handle(rollout())

        assert (first, second) == (True, False)
        assert len(orch.jobs.of_kind("rollback")) == 1

    @pytest.mark.asyncio
    async def test_a_second_replica_loses_the_claim(self, orch):
        orch.redis_client.set("mana:watch:claim:watch-production-sample-app-g2", "1", nx=True)

        assert await watcher_for(orch).handle(rollout()) is False
        assert orch.jobs.jobs == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        DeploymentStatus.RETRY_SCHEDULED, DeploymentStatus.ROLLING_BACK,
        DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK,
    ])
    async def test_a_rollout_a_webhook_already_dealt_with_is_left_alone(self, orch, status):
        orch.record_deployment_started("deploy-9-production", "production", "sample-app", "v2")
        state = orch.get_deployment_state("deploy-9-production")
        state.status = status
        orch.save_deployment_state(state)

        assert await watcher_for(orch).handle(rollout()) is False
        assert orch.jobs.jobs == []

    @pytest.mark.asyncio
    async def test_a_deployment_the_pipeline_is_still_running_is_not_left_alone(self, orch):
        orch.record_deployment_started("deploy-9-production", "production", "sample-app", "v2")

        assert await watcher_for(orch).handle(rollout()) is True

    @pytest.mark.asyncio
    async def test_another_version_in_the_history_does_not_matter(self, orch):
        orch.record_deployment_started("deploy-8-production", "production", "sample-app", "v1")
        state = orch.get_deployment_state("deploy-8-production")
        state.status = DeploymentStatus.ROLLED_BACK
        orch.save_deployment_state(state)

        assert await watcher_for(orch).handle(rollout()) is True

    @pytest.mark.asyncio
    async def test_redis_trouble_means_no_action(self, orch):
        down = redis.ConnectionError("down")
        orch.redis_client = Mock(**{"get.side_effect": down, "zrevrange.side_effect": down})

        assert await watcher_for(orch).handle(rollout()) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version", ["sha256:ab12cd34ef56", "latest"])
    async def test_a_rollout_that_cannot_be_matched_to_a_tag_alerts_instead_of_being_ignored(self, orch, version):
        orch.get_live_version = Mock(return_value=None)

        handled = await watcher_for(orch).handle(rollout(version=version))

        assert handled is True
        state = orch.get_deployment_state("watch-production-sample-app-g2")
        assert state.status is DeploymentStatus.FAILED
        assert orch.jobs.of_kind("rollback") == []

    @pytest.mark.asyncio
    async def test_a_pipeline_timeout_keeps_its_own_rule(self, orch):
        await orch.handle_deployment_failure(
            "d1", "production", "sample-app", "v2", FailureType.TIMEOUT, {"workflow_run_id": "9"}
        )

        assert orch.get_deployment_state("d1").status is DeploymentStatus.RETRY_SCHEDULED

    @pytest.mark.asyncio
    async def test_the_policy_can_decide_to_alert_instead(self, orch):
        from src.policy import DEFAULT_POLICY, Policy

        orch.policy = Policy.model_validate({**DEFAULT_POLICY, "default": {
            **DEFAULT_POLICY["default"], "rollout_stuck": {"action": "alert"}}})

        await watcher_for(orch).handle(rollout())

        assert orch.get_deployment_state("watch-production-sample-app-g2").status is DeploymentStatus.FAILED
        assert orch.jobs.of_kind("rollback") == []


class TestLoop:
    @pytest.mark.asyncio
    async def test_scan_once_handles_what_it_finds_and_survives_a_bad_rollout(self, orch):
        watcher = watcher_for(orch)
        good, bad = rollout(generation=3), rollout(generation=4)
        watcher.find_stuck = Mock(return_value=[bad, good])
        real = watcher.handle

        async def handle(stuck):
            if stuck is bad:
                raise RuntimeError("boom")
            return await real(stuck)

        watcher.handle = handle

        assert await watcher.scan_once() == 1

    @pytest.mark.asyncio
    async def test_run_scans_repeatedly_until_stopped(self, orch):
        watcher = watcher_for(orch, interval=0.01)
        watcher.scan_once = AsyncMock(return_value=0)
        task = asyncio.create_task(watcher.run())

        deadline = time.monotonic() + 3
        while watcher.scan_once.await_count < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        watcher.stop()
        await asyncio.wait_for(task, 3)

        assert watcher.scan_once.await_count >= 3

    @pytest.mark.asyncio
    async def test_stop_ends_a_long_wait_at_once(self, orch):
        watcher = watcher_for(orch, interval=3600)
        watcher.scan_once = AsyncMock(return_value=0)
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.05)

        watcher.stop()

        await asyncio.wait_for(task, 2)


def test_exactly_one_of_many_replicas_wins_the_claim_on_real_redis(real_redis, orch):
    from concurrent.futures import ThreadPoolExecutor

    orch.redis_client = real_redis
    watchers = [watcher_for(orch) for _ in range(16)]

    with ThreadPoolExecutor(16) as pool:
        wins = list(pool.map(lambda w: w._claim(rollout()), watchers))

    assert sum(wins) == 1
    assert 0 < real_redis.ttl("mana:watch:claim:watch-production-sample-app-g2") <= 60
