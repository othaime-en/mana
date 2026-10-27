class FakeRedis:
    """In-memory stand-in for the Redis calls the orchestrator makes"""

    def __init__(self):
        self.data = {}
        self.sorted_sets = {}

    def ping(self):
        return True

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, **_kwargs):
        self.data[key] = value

    def setex(self, key, _ttl, value):
        self.data[key] = value

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrem(self, key, *members):
        entries = self.sorted_sets.get(key, {})
        return sum(1 for member in members if entries.pop(member, None) is not None)

    def zrange(self, key, start, end):
        entries = self.sorted_sets.get(key, {})
        ordered = sorted(entries, key=entries.get)
        return ordered[start:] if end == -1 else ordered[start:end + 1]

    def zremrangebyscore(self, key, low, high):
        entries = self.sorted_sets.get(key, {})
        low = float("-inf") if low == "-inf" else float(low)
        high = float("inf") if high == "+inf" else float(high)
        stale = [member for member, score in entries.items() if low <= score <= high]
        for member in stale:
            del entries[member]
        return len(stale)
