"""Phase D acceptance: scheduled work survives restarts and replicas never double up.

Each Orchestrator below stands for one replica; they share a real Redis and a
fake cluster, like two pods sharing Redis and an API server.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from src.jobs import JobQueue, Scheduler
from src.locks import AppLocks
from src.orchestrator import DeploymentStatus, FailureType, SelfHealingOrchestrator


class Cluster:
    """The one Deployment both replicas act on"""

    def __init__(self, live="v2"):
        self.live = live
        self.patches = []

    def get_live_version(self, *_args):
        return self.live

    def rollback_deployment(self, namespace, name, target, deployment_id=None, **_kwargs):
        self.patches.append((deployment_id, target))
        time.sleep(0.3)
        self.live = target
        return True


def replica(real_redis, cluster, lock_ttl=2.0):
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        orch = SelfHealingOrchestrator(use_config=False)
    orch.redis_client = real_redis
    orch.jobs = JobQueue(real_redis)
    orch.locks = AppLocks(real_redis, ttl=lock_ttl)
    orch.initial_backoff = 0.3
    orch.max_retries = 1
    orch.get_previous_version = lambda *_: "v1"
    orch.get_live_version = cluster.get_live_version
    orch.rollback_deployment = cluster.rollback_deployment
    return orch


def scheduler_for(orch, lease=1.0):
    return Scheduler(
        orch.jobs,
        orch.job_handlers(),
        on_dead=orch.handle_dead_job,
        retry_delay=lambda attempts: 0.1,
        lease_seconds=lease,
        poll_seconds=0.02,
        max_attempts=4,
    )


async def fail(orch, deployment_id, run_id="9"):
    return await orch.handle_deployment_failure(
        deployment_id, "production", "sample-app", "v2",
        FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": run_id},
    )


async def until(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.03)
    return condition()


@pytest.mark.asyncio
async def test_a_scheduled_retry_survives_the_replica_that_scheduled_it(real_redis):
    cluster = Cluster()
    first = replica(real_redis, cluster)
    decision = await fail(first, "d1")
    assert decision["action"] == "retry"
    del first

    second = replica(real_redis, cluster)
    reruns = []
    second.rerun_workflow = reruns.append
    worker = scheduler_for(second)
    task = asyncio.create_task(worker.run())

    assert reruns == []
    assert await until(lambda: reruns)
    await worker.stop(5)
    await task

    assert reruns == ["9"]
    assert second.jobs.stats().pending == 0


@pytest.mark.asyncio
async def test_two_replicas_run_a_retry_once(real_redis):
    cluster = Cluster()
    one, two = replica(real_redis, cluster), replica(real_redis, cluster)
    await fail(one, "d1")
    reruns = []
    for orch in (one, two):
        orch.rerun_workflow = reruns.append
    workers = [scheduler_for(orch) for orch in (one, two)]
    tasks = [asyncio.create_task(w.run()) for w in workers]

    await until(lambda: reruns)
    await asyncio.sleep(0.5)
    await asyncio.gather(*(w.stop(5) for w in workers))
    await asyncio.gather(*tasks)

    assert reruns == ["9"]


@pytest.mark.asyncio
async def test_two_replicas_never_roll_the_same_app_back_twice(real_redis):
    cluster = Cluster()
    one, two = replica(real_redis, cluster), replica(real_redis, cluster)
    await fail(one, "d1")
    await fail(two, "d2")
    decisions = await asyncio.gather(fail(one, "d1"), fail(two, "d2"))
    assert [d["action"] for d in decisions] == ["rollback", "rollback"]
    workers = [scheduler_for(orch) for orch in (one, two)]
    tasks = [asyncio.create_task(w.run()) for w in workers]

    def settled():
        states = [one.get_deployment_state(i) for i in ("d1", "d2")]
        return all(s.status in (DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED) for s in states)

    assert await until(lambda: settled() and one.jobs.stats().pending == 0)
    await asyncio.gather(*(w.stop(5) for w in workers))
    await asyncio.gather(*tasks)

    assert len(cluster.patches) == 1
    statuses = sorted(one.get_deployment_state(i).status.value for i in ("d1", "d2"))
    assert statuses == ["failed", "rolled_back"]


@pytest.mark.asyncio
async def test_a_rollback_whose_worker_died_is_finished_by_another_replica(real_redis):
    cluster = Cluster()
    doomed, survivor = replica(real_redis, cluster, lock_ttl=1.0), replica(real_redis, cluster, lock_ttl=1.0)
    await fail(doomed, "d1")
    await fail(doomed, "d1")
    job = doomed.jobs.claim(1.0)
    assert job.kind == "rollback" and job.attempts == 1
    dead_lock = real_redis.lock(
        "mana:lock:rollback:production:sample-app", timeout=1.0, blocking=False, thread_local=False
    )
    assert dead_lock.acquire()
    cluster.live = "v1"
    del doomed

    worker = scheduler_for(survivor, lease=1.0)
    task = asyncio.create_task(worker.run())

    assert await until(
        lambda: survivor.get_deployment_state("d1").status is DeploymentStatus.ROLLED_BACK, timeout=10
    )
    await worker.stop(5)
    await task

    assert cluster.patches == [("d1", "v1")]
    assert survivor.jobs.stats().pending == survivor.jobs.stats().inflight == 0


@pytest.mark.asyncio
async def test_a_job_that_never_succeeds_ends_as_a_failed_deployment(real_redis):
    cluster = Cluster()
    orch = replica(real_redis, cluster)
    await fail(orch, "d1")

    def refuse(_run_id):
        raise RuntimeError("github down")

    orch.rerun_workflow = refuse
    worker = scheduler_for(orch)
    task = asyncio.create_task(worker.run())

    assert await until(lambda: orch.get_deployment_state("d1").status is DeploymentStatus.FAILED)
    await worker.stop(5)
    await task

    assert orch.jobs.stats().pending == orch.jobs.stats().inflight == 0


@pytest.mark.asyncio
async def test_a_failed_health_check_rolls_back_at_once_without_any_backoff(real_redis):
    cluster = Cluster()
    orch = replica(real_redis, cluster)
    orch.initial_backoff = 30.0
    worker = scheduler_for(orch)
    task = asyncio.create_task(worker.run())
    started = time.monotonic()

    decision = await orch.handle_deployment_failure(
        "d1", "production", "sample-app", "v2", FailureType.HEALTH_CHECK_FAILURE, {"workflow_run_id": "9"}
    )
    settled = await until(
        lambda: orch.get_deployment_state("d1").status is DeploymentStatus.ROLLED_BACK
    )
    elapsed = time.monotonic() - started
    await worker.stop(5)
    await task

    assert decision["action"] == "rollback"
    assert settled
    assert elapsed < 5
    assert cluster.patches == [("d1", "v1")]
