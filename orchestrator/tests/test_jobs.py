import asyncio
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import redis

from src import metrics
from src.jobs import (
    INFLIGHT_KEY,
    PENDING_KEY,
    Job,
    JobDeferred,
    JobQueue,
    PermanentJobError,
    QueueStats,
    Scheduler,
)
from tests.conftest import REDIS_TEST_URL


@pytest.fixture
def queue(real_redis):
    return JobQueue(real_redis)


class TestJobQueue:
    def test_a_due_job_is_claimed_once_with_its_payload(self, queue):
        queue.enqueue("retry_rerun", {"run": "1"}, job_id="j1")

        job = queue.claim(30)

        assert job == Job(id="j1", kind="retry_rerun", payload={"run": "1"}, attempts=1)
        assert queue.claim(30) is None

    def test_a_job_is_not_due_before_its_delay(self, queue):
        queue.enqueue("k", {}, delay=0.4, job_id="j1")

        assert queue.claim(30) is None
        time.sleep(0.5)
        assert queue.claim(30).id == "j1"

    def test_enqueueing_the_same_id_twice_adds_one_job(self, queue):
        assert queue.enqueue("k", {}, job_id="j1") is True
        assert queue.enqueue("k", {"other": 1}, job_id="j1") is False

        assert queue.claim(30).payload == {}
        assert queue.claim(30) is None

    def test_a_running_job_cannot_be_enqueued_again(self, queue):
        queue.enqueue("k", {}, job_id="j1")
        queue.claim(30)

        assert queue.enqueue("k", {}, job_id="j1") is False

    def test_jobs_are_claimed_oldest_first(self, queue):
        queue.enqueue("k", {}, delay=0.2, job_id="later")
        queue.enqueue("k", {}, job_id="first")
        time.sleep(0.3)

        assert [queue.claim(30).id, queue.claim(30).id] == ["first", "later"]

    def test_many_workers_never_claim_the_same_job(self, queue, real_redis):
        for i in range(40):
            queue.enqueue("k", {}, job_id=f"j{i}")
        queues = [JobQueue(real_redis) for _ in range(8)]
        claimed, lock = [], threading.Lock()

        def work(q):
            while (job := q.claim(30)) is not None:
                with lock:
                    claimed.append(job.id)

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(work, queues))

        assert sorted(claimed) == sorted(f"j{i}" for i in range(40))

    def test_an_abandoned_job_returns_when_its_lease_expires(self, queue):
        queue.enqueue("k", {}, job_id="j1")
        queue.claim(0.3)

        assert queue.reap_expired() == []
        time.sleep(0.4)
        assert queue.reap_expired() == ["j1"]

        again = queue.claim(30)
        assert again.id == "j1" and again.attempts == 2

    def test_extending_the_lease_keeps_the_job_with_its_worker(self, queue):
        queue.enqueue("k", {}, job_id="j1")
        queue.claim(0.4)
        time.sleep(0.25)
        assert queue.extend("j1", 0.6) is True
        time.sleep(0.3)

        assert queue.reap_expired() == []

    def test_extending_a_lost_lease_reports_it(self, queue):
        assert queue.extend("ghost", 30) is False

    def test_completed_jobs_disappear(self, queue, real_redis):
        queue.enqueue("k", {}, job_id="j1")
        queue.claim(30)
        queue.complete("j1")

        assert real_redis.zcard(INFLIGHT_KEY) == 0
        assert real_redis.exists("mana:job:j1") == 0
        assert queue.enqueue("k", {}, job_id="j1") is True

    def test_rescheduled_jobs_keep_their_attempts(self, queue):
        queue.enqueue("k", {}, job_id="j1")
        job = queue.claim(30)
        queue.reschedule(job, 0.2)

        assert queue.claim(30) is None
        time.sleep(0.3)
        assert queue.claim(30).attempts == 2

    def test_a_job_whose_body_expired_is_dropped(self, queue, real_redis):
        queue.enqueue("k", {}, job_id="j1")
        real_redis.delete("mana:job:j1")

        assert queue.claim(30) is None
        assert real_redis.zcard(INFLIGHT_KEY) == 0 and real_redis.zcard(PENDING_KEY) == 0

    def test_stats_report_depth_and_the_wait_of_the_oldest_due_job(self, queue):
        assert queue.stats() == QueueStats(0, 0, 0.0)
        queue.enqueue("k", {}, job_id="due")
        queue.enqueue("k", {}, delay=60, job_id="later")
        time.sleep(0.3)
        queue.claim(30)
        queue.enqueue("k", {}, job_id="waiting")
        time.sleep(0.3)

        stats = queue.stats()

        assert (stats.pending, stats.inflight) == (2, 1)
        assert 0.25 < stats.overdue_seconds < 2

    def test_a_job_survives_its_worker_being_killed(self, queue):
        queue.enqueue("retry_rerun", {"run": "7"}, job_id="j1")
        script = textwrap.dedent(f"""
            import time, redis
            from src.jobs import JobQueue
            q = JobQueue(redis.Redis.from_url({REDIS_TEST_URL!r}, decode_responses=True))
            job = q.claim(1.0)
            print("claimed", job.id, flush=True)
            time.sleep(60)
        """)
        worker = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            assert worker.stdout.readline().strip() == "claimed j1"
            worker.kill()
            worker.wait()
        finally:
            worker.kill()

        assert queue.claim(30) is None
        time.sleep(1.1)
        assert queue.reap_expired() == ["j1"]
        recovered = queue.claim(30)

        assert recovered.payload == {"run": "7"} and recovered.attempts == 2


def make_scheduler(queue, handlers, **kwargs):
    defaults = dict(
        on_dead=Mock(),
        retry_delay=lambda attempts: 0.05,
        lease_seconds=1.0,
        poll_seconds=0.02,
        max_attempts=3,
    )
    defaults.update(kwargs)
    return Scheduler(queue, handlers, **defaults)


async def run_until(scheduler, condition, timeout=5.0):
    task = asyncio.create_task(scheduler.run())
    try:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        await scheduler.stop(grace_seconds=5)
        await task
    return condition()


def counter(kind, outcome):
    return metrics.scheduler_jobs_total.labels(kind=kind, outcome=outcome)._value.get()


@pytest.mark.asyncio
class TestScheduler:
    async def test_a_due_job_is_handled_and_removed(self, queue, real_redis):
        seen = []
        queue.enqueue("echo", {"n": 1}, job_id="j1")
        before = counter("echo", "completed")
        scheduler = make_scheduler(queue, {"echo": lambda job: seen.append(job.payload)})

        assert await run_until(scheduler, lambda: seen)

        assert seen == [{"n": 1}]
        await asyncio.sleep(0.1)
        assert real_redis.zcard(INFLIGHT_KEY) == real_redis.zcard(PENDING_KEY) == 0
        assert counter("echo", "completed") == before + 1

    async def test_a_failing_job_is_retried_with_delay_then_declared_dead(self, queue):
        calls, on_dead = [], Mock()
        queue.enqueue("flaky", {}, job_id="j1")

        def boom(job):
            calls.append(job.attempts)
            raise RuntimeError("github down")

        scheduler = make_scheduler(queue, {"flaky": boom}, on_dead=on_dead, max_attempts=3)

        assert await run_until(scheduler, lambda: on_dead.called)

        assert calls == [1, 2, 3]
        job, error = on_dead.call_args.args
        assert job.id == "j1" and str(error) == "github down"
        assert queue.stats() == QueueStats(0, 0, 0.0)

    async def test_a_permanent_error_skips_the_retries(self, queue):
        calls, on_dead = [], Mock()
        queue.enqueue("bad", {}, job_id="j1")

        def refuse(job):
            calls.append(1)
            raise PermanentJobError("401 unauthorized")

        scheduler = make_scheduler(queue, {"bad": refuse}, on_dead=on_dead)

        assert await run_until(scheduler, lambda: on_dead.called)
        assert calls == [1]

    async def test_an_unknown_kind_is_dead_lettered(self, queue):
        on_dead = Mock()
        queue.enqueue("mystery", {}, job_id="j1")

        assert await run_until(make_scheduler(queue, {}, on_dead=on_dead), lambda: on_dead.called)
        assert "no handler" in str(on_dead.call_args.args[1])

    async def test_deferring_does_not_use_up_attempts(self, queue):
        calls, on_dead = [], Mock()
        queue.enqueue("busy", {}, job_id="j1")

        def handler(job):
            calls.append(job.attempts)
            if len(calls) < 5:
                raise JobDeferred(0.05)

        scheduler = make_scheduler(queue, {"busy": handler}, on_dead=on_dead, max_attempts=2)

        assert await run_until(scheduler, lambda: len(calls) == 5)
        assert calls == [1, 1, 1, 1, 1]
        on_dead.assert_not_called()

    async def test_the_lease_is_renewed_while_a_long_job_runs(self, queue):
        runs = []
        queue.enqueue("slow", {}, job_id="j1")

        def slow(job):
            runs.append(job.id)
            time.sleep(1.6)

        first = make_scheduler(queue, {"slow": slow}, lease_seconds=0.6)
        second = make_scheduler(queue, {"slow": slow}, lease_seconds=0.6)
        t1, t2 = asyncio.create_task(first.run()), asyncio.create_task(second.run())
        await asyncio.sleep(2.4)
        await asyncio.gather(first.stop(5), second.stop(5))
        await asyncio.gather(t1, t2)

        assert runs == ["j1"]

    async def test_two_replicas_run_every_job_exactly_once(self, queue, real_redis):
        seen, lock = [], threading.Lock()

        def record(job):
            with lock:
                seen.append(job.id)

        for i in range(30):
            queue.enqueue("echo", {}, job_id=f"j{i}")
        replicas = [
            make_scheduler(JobQueue(real_redis), {"echo": record}, max_concurrency=3)
            for _ in range(2)
        ]
        tasks = [asyncio.create_task(r.run()) for r in replicas]
        deadline = time.monotonic() + 8
        while len(seen) < 30 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.gather(*(r.stop(5) for r in replicas))
        await asyncio.gather(*tasks)

        assert sorted(seen) == sorted(f"j{i}" for i in range(30))

    async def test_a_job_that_lost_its_worker_is_picked_up_by_another(self, queue):
        seen = []
        queue.enqueue("echo", {}, job_id="j1")
        queue.claim(0.3)
        scheduler = make_scheduler(queue, {"echo": lambda job: seen.append(job.attempts)})

        assert await run_until(scheduler, lambda: seen)
        assert seen == [2]

    async def test_stop_waits_for_running_jobs(self, queue):
        finished = []
        queue.enqueue("slow", {}, job_id="j1")

        def slow(job):
            time.sleep(0.4)
            finished.append(job.id)

        scheduler = make_scheduler(queue, {"slow": slow})
        task = asyncio.create_task(scheduler.run())
        while scheduler.running_jobs == 0:
            await asyncio.sleep(0.02)
        await scheduler.stop(grace_seconds=5)
        await task

        assert finished == ["j1"]

    async def test_a_tick_failure_does_not_stop_the_scheduler(self, queue):
        seen = []
        queue.enqueue("echo", {}, job_id="j1")
        scheduler = make_scheduler(queue, {"echo": lambda job: seen.append(1)})
        real = queue.reap_expired
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise redis.ConnectionError("down")
            return real()

        queue.reap_expired = flaky

        assert await run_until(scheduler, lambda: seen)


def test_collector_reports_queue_depth():
    stats = QueueStats(pending=3, inflight=1, overdue_seconds=42.5)

    families = {f.name: f.samples[0].value for f in metrics.SchedulerCollector(lambda: stats).collect()}

    assert families == {
        "mana_scheduler_pending_jobs": 3,
        "mana_scheduler_inflight_jobs": 1,
        "mana_scheduler_overdue_seconds": 42.5,
    }


def test_collector_stays_silent_when_redis_is_down():
    def down():
        raise redis.ConnectionError("down")

    assert list(metrics.SchedulerCollector(down).collect()) == []


def test_the_queue_and_locks_work_through_the_counting_wrapper(real_redis):
    from src.locks import AppLocks

    wrapped = metrics.CountingRedis(real_redis)
    queue = JobQueue(wrapped)
    queue.enqueue("k", {"a": 1}, job_id="j1")

    assert queue.claim(30).payload == {"a": 1}
    assert queue.stats().inflight == 1
    with AppLocks(wrapped, ttl=5).acquire("production", "sample-app"):
        assert AppLocks(wrapped, ttl=5).acquire("production", "sample-app") is None


def test_script_connection_errors_are_counted():
    unreachable = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    queue = JobQueue(metrics.CountingRedis(unreachable))
    before = metrics.redis_connection_errors_total._value.get()

    with pytest.raises(redis.ConnectionError):
        queue.stats()

    assert metrics.redis_connection_errors_total._value.get() == before + 1
