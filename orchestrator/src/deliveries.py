"""Drop replayed and duplicate webhook deliveries.

A signed request is identified by its signature, which covers the timestamp and
the body. The first delivery claims a key in Redis; a copy that arrives later
(a captured request replayed inside the signature window, a retried curl, or
the same request reaching both replicas) gets the first one's answer instead of
being processed again. A claim that was never finished expires on its own, and
one whose processing failed is released so the sender may retry.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "mana:delivery:"
PROCESSING_TTL_SECONDS = 30
RESULT_TTL_SECONDS = 600


@dataclass
class Claim:
    """The outcome of claiming a delivery: new, still running, or already answered"""
    key: Optional[str]
    state: str
    status_code: int = 0
    body: Any = None

    @property
    def is_new(self) -> bool:
        return self.state == "new"


class Deliveries:
    def __init__(self, client):
        self._redis = client

    @staticmethod
    def _key(signature: str) -> str:
        return KEY_PREFIX + hashlib.sha256(signature.encode()).hexdigest()[:32]

    def begin(self, signature: str) -> Claim:
        """Claim a delivery; the first caller gets state 'new', copies do not"""
        key = self._key(signature)
        for _ in range(2):
            if self._redis.set(
                key, json.dumps({"state": "processing"}), nx=True, ex=PROCESSING_TTL_SECONDS
            ):
                return Claim(key, "new")
            raw = self._redis.get(key)
            if raw is None:
                continue
            record = json.loads(raw)
            if record.get("state") == "done":
                return Claim(key, "done", record["status_code"], record["body"])
            return Claim(key, "processing")
        return Claim(key, "processing")

    def finish(self, claim: Claim, status_code: int, body: Any) -> None:
        """Remember the answer so copies of this delivery receive it"""
        self._redis.set(
            claim.key,
            json.dumps({"state": "done", "status_code": status_code, "body": body}),
            ex=RESULT_TTL_SECONDS,
        )

    def abandon(self, claim: Claim) -> None:
        """Release a claim whose processing failed so the sender can retry"""
        try:
            self._redis.delete(claim.key)
        except redis.RedisError:
            logger.warning("Could not release delivery claim %s; it expires on its own", claim.key)
