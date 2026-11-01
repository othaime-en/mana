from src.jobs import Job, QueueStats


class FakeJobQueue:
    """Records enqueued jobs; the handlers can be run by hand with job()"""

    def __init__(self):
        self.jobs = []

    def enqueue(self, kind, payload, delay=0.0, job_id=None):
        if job_id is not None and any(item["job_id"] == job_id for item in self.jobs):
            return False
        self.jobs.append({"kind": kind, "payload": payload, "delay": delay, "job_id": job_id})
        return True

    def of_kind(self, kind):
        return [item for item in self.jobs if item["kind"] == kind]

    def job(self, index=-1):
        item = self.jobs[index]
        return Job(id=item["job_id"], kind=item["kind"], payload=item["payload"], attempts=1)

    def stats(self):
        return QueueStats(pending=len(self.jobs))


class FakeLocks:
    """Per-application locks held in memory"""

    def __init__(self):
        self.held = set()

    def acquire(self, namespace, app):
        key = (namespace, app)
        if key in self.held:
            return None
        self.held.add(key)
        return _Held(self, key)


class _Held:
    def __init__(self, locks, key):
        self._locks, self._key = locks, key

    def release(self):
        self._locks.held.discard(self._key)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()


class FakeRedis:
    """In-memory stand-in for the Redis calls the orchestrator makes"""

    def __init__(self):
        self.data = {}
        self.sorted_sets = {}

    def ping(self):
        return True

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, get=False, **_kwargs):
        previous = self.data.get(key)
        self.data[key] = value
        return previous if get else None

    def setex(self, key, _ttl, value):
        self.data[key] = value

    def mget(self, keys):
        return [self.data.get(key) for key in keys]

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrem(self, key, *members):
        entries = self.sorted_sets.get(key, {})
        return sum(1 for member in members if entries.pop(member, None) is not None)

    def zrange(self, key, start, end):
        entries = self.sorted_sets.get(key, {})
        ordered = sorted(entries, key=entries.get)
        return ordered[start:] if end == -1 else ordered[start:end + 1]

    def zrevrange(self, key, start, end):
        entries = self.sorted_sets.get(key, {})
        ordered = sorted(entries, key=entries.get, reverse=True)
        return ordered[start:] if end == -1 else ordered[start:end + 1]

    def zremrangebyrank(self, key, start, end):
        entries = self.sorted_sets.get(key, {})
        ordered = sorted(entries, key=entries.get)
        size = len(ordered)
        first = start if start >= 0 else max(size + start, 0)
        last = end if end >= 0 else size + end
        if last < 0:
            return 0
        doomed = ordered[first:last + 1]
        for member in doomed:
            del entries[member]
        return len(doomed)

    def zremrangebyscore(self, key, low, high):
        entries = self.sorted_sets.get(key, {})
        low = float("-inf") if low == "-inf" else float(low)
        high = float("inf") if high == "+inf" else float(high)
        stale = [member for member, score in entries.items() if low <= score <= high]
        for member in stale:
            del entries[member]
        return len(stale)
