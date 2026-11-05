"""Durable jobs on Redis: a leased queue and the scheduler that works it.

A job waits in a sorted set scored by the time it is due. A worker claims it
atomically, which moves it to an in-flight set scored by the end of its lease.
The worker keeps extending the lease while it runs. If the worker dies, the
lease expires and any replica puts the job back, so no job is lost and none
runs twice at the same time. Every timestamp comes from the Redis server, so
replicas with different clocks agree.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import redis

from src import metrics

logger = logging.getLogger(__name__)

PENDING_KEY = "mana:jobs:pending"
INFLIGHT_KEY = "mana:jobs:inflight"
JOB_KEY_PREFIX = "mana:job:"
JOB_TTL_SECONDS = 86400

_NOW = (
    "local t = redis.call('TIME')\n"
    "local now = tonumber(t[1]) + tonumber(t[2]) / 1000000\n"
)

ENQUEUE_SCRIPT = _NOW + """
if redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', ARGV[3]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[4])
  return 1
end
return 0
"""

CLAIM_SCRIPT = _NOW + """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, 1)
if #ids == 0 then return false end
redis.call('ZREM', KEYS[1], ids[1])
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[1]), ids[1])
return ids[1]
"""

EXTEND_SCRIPT = _NOW + """
if redis.call('ZSCORE', KEYS[1], ARGV[2]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[2])
  return 1
end
return 0
"""

REAP_SCRIPT = _NOW + """
local ids = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)
for _, id in ipairs(ids) do
  redis.call('ZREM', KEYS[2], id)
  redis.call('ZADD', KEYS[1], now, id)
end
return ids
"""

RESCHEDULE_SCRIPT = _NOW + """
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[2])
return 1
"""

STATS_SCRIPT = _NOW + """
local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local overdue = 0
if #first == 2 then overdue = math.max(now - tonumber(first[2]), 0) end
return {redis.call('ZCARD', KEYS[1]), redis.call('ZCARD', KEYS[2]), tostring(overdue)}
"""


class JobDeferred(Exception):
    """Run the job again later without counting the attempt as a failure"""

    def __init__(self, delay: float, reason: str = ""):
        super().__init__(reason or f"deferred for {delay}s")
        self.delay = delay


class PermanentJobError(Exception):
    """The job cannot succeed however often it is retried"""


@dataclass
class Job:
    """A unit of work with its payload and how often it has been claimed"""
    id: str
    kind: str
    payload: Dict[str, Any]
    attempts: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "kind": self.kind, "payload": self.payload, "attempts": self.attempts}
        )

    @classmethod
    def from_json(cls, raw: str) -> "Job":
        data = json.loads(raw)
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data.get("payload", {}),
            attempts=int(data.get("attempts", 0)),
        )


@dataclass
class QueueStats:
    pending: int = 0
    inflight: int = 0
    overdue_seconds: float = 0.0


class JobQueue:
    """Leased job queue stored in Redis"""

    def __init__(self, client):
        self._redis = client
        self._enqueue = client.register_script(ENQUEUE_SCRIPT)
        self._claim = client.register_script(CLAIM_SCRIPT)
        self._extend = client.register_script(EXTEND_SCRIPT)
        self._reap = client.register_script(REAP_SCRIPT)
        self._reschedule = client.register_script(RESCHEDULE_SCRIPT)
        self._stats = client.register_script(STATS_SCRIPT)

    @staticmethod
    def _body_key(job_id: str) -> str:
        return f"{JOB_KEY_PREFIX}{job_id}"

    def enqueue(
        self,
        kind: str,
        payload: Dict[str, Any],
        delay: float = 0.0,
        job_id: Optional[str] = None,
    ) -> bool:
        """
        Add a job that becomes due after delay seconds

        A job id that is already queued or running is not added again, which
        makes enqueueing idempotent. Returns True when the job was added.
        """
        if job_id is None:
            job_id = f"{kind}:{time.time_ns()}"
        job = Job(id=job_id, kind=kind, payload=payload)
        added = self._enqueue(
            keys=[PENDING_KEY, self._body_key(job_id)],
            args=[max(delay, 0), job.to_json(), JOB_TTL_SECONDS, job_id],
        )
        return bool(added)

    def claim(self, lease_seconds: float) -> Optional[Job]:
        """Take the oldest due job and lease it, or return None"""
        job_id = self._claim(keys=[PENDING_KEY, INFLIGHT_KEY], args=[lease_seconds])
        if not job_id:
            return None
        raw = self._redis.get(self._body_key(job_id))
        if raw is None:
            logger.warning("Dropping job %s: its body expired", job_id)
            self._redis.zrem(INFLIGHT_KEY, job_id)
            return None
        job = Job.from_json(raw)
        job.attempts += 1
        self._redis.set(self._body_key(job.id), job.to_json(), ex=JOB_TTL_SECONDS)
        return job

    def extend(self, job_id: str, lease_seconds: float) -> bool:
        """Push the lease of a running job forward; False if it was lost"""
        return bool(
            self._extend(keys=[INFLIGHT_KEY], args=[lease_seconds, job_id])
        )

    def complete(self, job_id: str) -> None:
        """Remove a finished job"""
        self._redis.zrem(INFLIGHT_KEY, job_id)
        self._redis.delete(self._body_key(job_id))

    def reschedule(self, job: Job, delay: float) -> None:
        """Put a claimed job back to run after delay seconds"""
        self._redis.set(self._body_key(job.id), job.to_json(), ex=JOB_TTL_SECONDS)
        self._reschedule(
            keys=[PENDING_KEY, INFLIGHT_KEY], args=[max(delay, 0), job.id]
        )

    def reap_expired(self) -> List[str]:
        """Return jobs whose worker stopped renewing the lease to the queue"""
        return list(self._reap(keys=[PENDING_KEY, INFLIGHT_KEY]))

    def stats(self) -> QueueStats:
        """Queue depth and how long the oldest due job has been waiting"""
        pending, inflight, overdue = self._stats(keys=[PENDING_KEY, INFLIGHT_KEY])
        return QueueStats(int(pending), int(inflight), float(overdue))


Handler = Callable[[Job], None]
DeadLetter = Callable[[Job, Exception], None]


class Scheduler:
    """
    Run due jobs on every replica

    Handlers are blocking functions and run in worker threads. A handler may
    raise JobDeferred to run again later, PermanentJobError to give up at once,
    or any other exception to be retried with backoff until max_attempts, after
    which on_dead is called.
    """

    def __init__(
        self,
        queue: JobQueue,
        handlers: Dict[str, Handler],
        on_dead: DeadLetter,
        retry_delay: Callable[[int], float],
        lease_seconds: float = 60.0,
        poll_seconds: float = 1.0,
        max_concurrency: int = 4,
        max_attempts: int = 5,
    ):
        self.queue = queue
        self.handlers = handlers
        self.on_dead = on_dead
        self.retry_delay = retry_delay
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.max_concurrency = max_concurrency
        self.max_attempts = max_attempts
        self._tasks: set = set()
        self._stopping = False
        self._wake: Optional[asyncio.Event] = None

    @property
    def running_jobs(self) -> int:
        return len(self._tasks)

    async def run(self) -> None:
        """Claim and execute jobs until stop() is called"""
        self._stopping = False
        self._wake = asyncio.Event()
        logger.info("Scheduler started (lease %ss)", self.lease_seconds)
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
            except asyncio.TimeoutError:
                pass
        logger.info("Scheduler stopped claiming jobs")

    async def tick(self) -> None:
        """Requeue expired leases, then fill the free worker slots"""
        for job_id in await asyncio.to_thread(self.queue.reap_expired):
            logger.warning("Lease expired for job %s; requeued", job_id)
            metrics.scheduler_jobs_total.labels(kind="unknown", outcome="lease_expired").inc()
        while len(self._tasks) < self.max_concurrency and not self._stopping:
            job = await asyncio.to_thread(self.queue.claim, self.lease_seconds)
            if job is None:
                break
            task = asyncio.create_task(self._execute(job), name=f"job-{job.id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def stop(self, grace_seconds: float = 30.0) -> None:
        """Stop claiming and wait for running jobs; leftovers expire and requeue"""
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=grace_seconds)
            for task in pending:
                logger.warning("Abandoning %s; its lease will expire", task.get_name())

    async def _heartbeat(self, job_id: str) -> None:
        interval = self.lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            try:
                if not await asyncio.to_thread(self.queue.extend, job_id, self.lease_seconds):
                    logger.error("Lost the lease on job %s", job_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Could not extend the lease on job %s", job_id, exc_info=True)

    async def _execute(self, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            if handler is None:
                raise PermanentJobError(f"no handler for job kind {job.kind}")
            await asyncio.to_thread(handler, job)
        except JobDeferred as deferred:
            job.attempts -= 1
            await self._finish(job, "deferred", self.queue.reschedule, job, deferred.delay)
        except PermanentJobError as error:
            await self._give_up(job, error)
        except Exception as error:
            if job.attempts >= self.max_attempts:
                await self._give_up(job, error)
            else:
                logger.warning(
                    "Job %s failed (attempt %s/%s): %s",
                    job.id, job.attempts, self.max_attempts, error,
                )
                await self._finish(
                    job, "retried", self.queue.reschedule, job, self.retry_delay(job.attempts)
                )
        else:
            await self._finish(job, "completed", self.queue.complete, job.id)
        finally:
            heartbeat.cancel()

    async def _give_up(self, job: Job, error: Exception) -> None:
        logger.error("Job %s is dead: %s", job.id, error)
        try:
            await asyncio.to_thread(self.on_dead, job, error)
        except Exception:
            logger.exception("Dead-letter handling failed for job %s", job.id)
        await self._finish(job, "dead", self.queue.complete, job.id)

    async def _finish(self, job: Job, outcome: str, operation: Callable, *args: Any) -> None:
        try:
            await asyncio.to_thread(operation, *args)
        except redis.RedisError:
            logger.exception("Could not record %s for job %s; the lease will expire", outcome, job.id)
            return
        metrics.scheduler_jobs_total.labels(kind=job.kind, outcome=outcome).inc()
