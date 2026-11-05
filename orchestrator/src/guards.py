"""Guards that stop recovery from repeating itself.

The deny-list remembers versions that had to be rolled back, so a re-run or a
later pipeline cannot put the same broken version back. The circuit breaker
counts recent rollbacks of an application: when there are too many, something
is wrong that rolling back again will not fix, and automation pauses until a
person looks. Both live in Redis so every replica agrees.

Reads fail open. If Redis cannot be read, nothing is denied and the breaker
stays closed, because a guard that cannot answer must not block a deployment or
a recovery.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import redis

logger = logging.getLogger(__name__)

DENIED_PREFIX = "mana:denied"
DENIED_INDEX_PREFIX = "mana:denied-index"
ROLLBACKS_PREFIX = "mana:rollbacks"


@dataclass(frozen=True)
class BreakerState:
    """How many rollbacks an application had inside the window, and whether that is too many"""
    open: bool
    rollbacks: int
    max_rollbacks: int
    window_seconds: int


class Guards:
    """Deny-list and circuit breaker over a Redis client"""

    def __init__(self, client):
        self._redis = client

    @staticmethod
    def _denied_key(namespace: str, app: str, version: str) -> str:
        return f"{DENIED_PREFIX}:{namespace}:{app}:{version}"

    @staticmethod
    def _index_key(namespace: str, app: str) -> str:
        return f"{DENIED_INDEX_PREFIX}:{namespace}:{app}"

    @staticmethod
    def _rollbacks_key(namespace: str, app: str) -> str:
        return f"{ROLLBACKS_PREFIX}:{namespace}:{app}"

    def deny(
        self,
        namespace: str,
        app: str,
        version: str,
        ttl_seconds: int,
        reason: str,
        deployment_id: str
    ) -> None:
        """Refuse to deploy this version for ttl_seconds"""
        now = time.time()
        entry = {
            "version": version,
            "since": now,
            "expires_at": now + ttl_seconds,
            "reason": reason,
            "deployment_id": deployment_id,
        }
        self._redis.set(self._denied_key(namespace, app, version), json.dumps(entry), ex=ttl_seconds)
        self._redis.zadd(self._index_key(namespace, app), {version: entry["expires_at"]})

    def denied(self, namespace: str, app: str, version: str) -> Optional[Dict]:
        """The deny-list entry for a version, or None when it may be deployed"""
        try:
            raw = self._redis.get(self._denied_key(namespace, app, version))
            return json.loads(raw) if raw else None
        except (redis.RedisError, TypeError, ValueError) as error:
            logger.warning("Cannot read the deny-list, allowing %s: %s", version, error)
            return None

    def allow(self, namespace: str, app: str, version: str) -> bool:
        """Take a version off the deny-list; False when it was not on it"""
        removed = self._redis.delete(self._denied_key(namespace, app, version))
        self._redis.zrem(self._index_key(namespace, app), version)
        return bool(removed)

    def denied_versions(self, namespace: str, app: str) -> List[Dict]:
        """Every version currently denied for the application, newest first"""
        index = self._index_key(namespace, app)
        try:
            self._redis.zremrangebyscore(index, "-inf", time.time())
            versions = self._redis.zrange(index, 0, -1)
            raws = self._redis.mget([self._denied_key(namespace, app, v) for v in versions]) if versions else []
        except redis.RedisError as error:
            logger.warning("Cannot list the deny-list: %s", error)
            return []
        entries = [json.loads(raw) for raw in raws if raw]
        return sorted(entries, key=lambda entry: entry["since"], reverse=True)

    def record_rollback(self, namespace: str, app: str, deployment_id: str, window_seconds: int) -> None:
        """Count a finished rollback; one entry per deployment, so a repeated job counts once"""
        key = self._rollbacks_key(namespace, app)
        now = time.time()
        self._redis.zadd(key, {deployment_id: now})
        self._redis.zremrangebyscore(key, "-inf", now - window_seconds)

    def breaker(self, namespace: str, app: str, max_rollbacks: int, window_seconds: int) -> BreakerState:
        """Whether the application has rolled back too often inside the window"""
        key = self._rollbacks_key(namespace, app)
        try:
            self._redis.zremrangebyscore(key, "-inf", time.time() - window_seconds)
            count = int(self._redis.zcard(key))
        except redis.RedisError as error:
            logger.warning("Cannot read the circuit breaker, leaving it closed: %s", error)
            count = 0
        return BreakerState(count >= max_rollbacks, count, max_rollbacks, window_seconds)
