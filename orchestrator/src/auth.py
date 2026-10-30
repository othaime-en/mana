"""Authentication for the orchestrator API.

Webhooks carry an HMAC-SHA256 signature over "<timestamp>.<body>" so a captured
request cannot be altered, and goes stale after TIMESTAMP_TOLERANCE_SECONDS.
Operator endpoints take an API key. Both accept several values so a secret can
be rotated without downtime.
"""

import hashlib
import hmac
import logging
import time
from typing import Iterable, Optional

from fastapi import Header, HTTPException, Request

from src.config import OrchestratorConfig, get_config

logger = logging.getLogger(__name__)

TIMESTAMP_HEADER = "X-Mana-Timestamp"
SIGNATURE_HEADER = "X-Mana-Signature"
API_KEY_HEADER = "X-API-Key"
TIMESTAMP_TOLERANCE_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Return the signature header value for a webhook body"""
    digest = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def signature_failure(
    secrets: Iterable[str],
    timestamp: Optional[str],
    signature: Optional[str],
    body: bytes,
    now: Optional[float] = None,
) -> Optional[str]:
    """Return why a webhook is not authentic ('missing', 'stale', 'invalid') or None"""
    if not timestamp or not signature:
        return "missing"
    try:
        sent_at = int(timestamp)
    except ValueError:
        return "invalid"
    current = time.time() if now is None else now
    if abs(current - sent_at) > TIMESTAMP_TOLERANCE_SECONDS:
        return "stale"
    matches = [
        hmac.compare_digest(sign(secret, timestamp, body), signature)
        for secret in secrets
    ]
    return None if any(matches) else "invalid"


def key_matches(keys: Iterable[str], presented: Optional[str]) -> bool:
    """Constant-time comparison against every configured key"""
    if not presented:
        return False
    matches = [hmac.compare_digest(key.encode(), presented.encode()) for key in keys]
    return any(matches)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def require_signed_webhook(request: Request) -> None:
    """FastAPI dependency: reject webhooks without a valid fresh signature"""
    config = get_config()
    if config.auth_disabled:
        return
    body = await request.body()
    reason = signature_failure(
        config.webhook_secrets,
        request.headers.get(TIMESTAMP_HEADER),
        request.headers.get(SIGNATURE_HEADER),
        body,
    )
    if reason:
        logger.warning(
            "Rejected webhook from %s on %s: %s signature", _client(request), request.url.path, reason
        )
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")


async def require_api_key(
    request: Request, x_api_key: Optional[str] = Header(default=None)
) -> None:
    """FastAPI dependency: reject operator requests without a valid API key"""
    config = get_config()
    if config.auth_disabled:
        return
    if not key_matches(config.api_keys, x_api_key):
        logger.warning("Rejected %s %s from %s: invalid API key", request.method, request.url.path, _client(request))
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def warn_if_disabled(config: OrchestratorConfig) -> None:
    """Make a disabled-auth deployment impossible to miss in the logs"""
    if config.auth_disabled:
        logger.critical(
            "AUTH_DISABLED=true: every endpoint is unauthenticated. Never use this outside local development"
        )
