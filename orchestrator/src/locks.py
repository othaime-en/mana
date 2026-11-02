"""Per-application locks so two replicas never act on the same app at once.

The lock expires on its own after ttl seconds, so a crashed holder frees it,
and a background thread renews it for as long as the holder is alive.
"""

import logging
import threading
from typing import Optional

import redis
from redis.exceptions import LockError

logger = logging.getLogger(__name__)

LOCK_KEY_PREFIX = "mana:lock:rollback"


class HeldLock:
    """A lock this process holds and keeps renewing until release()"""

    def __init__(self, lock, ttl: float, name: str):
        self._lock = lock
        self._ttl = ttl
        self._name = name
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(
            target=self._renew, name=f"lock-renew-{name}", daemon=True
        )
        self._thread.start()

    def _renew(self) -> None:
        while not self._stop.wait(self._ttl / 3):
            try:
                self._lock.extend(self._ttl, replace_ttl=True)
            except (LockError, redis.RedisError):
                self.lost = True
                logger.error("Lost the lock %s while holding it", self._name)
                return

    def release(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._lock.release()
        except (LockError, redis.RedisError) as error:
            logger.warning("Could not release the lock %s: %s", self._name, error)

    def __enter__(self) -> "HeldLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


class AppLocks:
    """Hands out one lock per (namespace, application)"""

    def __init__(self, client, ttl: float = 60.0):
        self._redis = client
        self.ttl = ttl

    def acquire(self, namespace: str, app: str) -> Optional[HeldLock]:
        """Take the lock without waiting; None when someone else holds it"""
        name = f"{LOCK_KEY_PREFIX}:{namespace}:{app}"
        lock = self._redis.lock(name, timeout=self.ttl, blocking=False, thread_local=False)
        if not lock.acquire():
            return None
        return HeldLock(lock, self.ttl, name)
