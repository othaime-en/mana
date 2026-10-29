"""Prometheus metrics emitted by the orchestrator"""

import logging
from typing import Callable, Dict, Iterable, Optional

import redis
from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

logger = logging.getLogger(__name__)

DEPLOYMENT_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600)
RECOVERY_BUCKETS = (5, 10, 20, 30, 45, 60, 90, 120, 300, 600, 1800)
DECISION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
HEALTH_CHECK_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600)

deployments_total = Counter(
    'deployments_total',
    'Deployments that reached a final status, counted once per transition',
    ['namespace', 'status']
)
rollbacks_total = Counter(
    'rollbacks_total',
    'Total number of rollbacks',
    ['namespace', 'reason']
)
retries_total = Counter(
    'retries_total',
    'Total number of deployment retries',
    ['namespace', 'retry_attempt']
)
deployment_duration_seconds = Histogram(
    'deployment_duration_seconds',
    'Seconds from the first in_progress event to the final status',
    ['namespace', 'status'],
    buckets=DEPLOYMENT_BUCKETS
)
mana_recovery_duration_seconds = Histogram(
    'mana_recovery_duration_seconds',
    'Seconds from the first failure to a successful retry or a verified rollback',
    ['namespace', 'app', 'outcome'],
    buckets=RECOVERY_BUCKETS
)
rollback_decision_duration_seconds = Histogram(
    'rollback_decision_duration_seconds',
    'Seconds the orchestrator needs to decide how to handle a failure',
    buckets=DECISION_BUCKETS
)
health_check_duration_seconds = Histogram(
    'health_check_duration_seconds',
    'Health check duration in seconds',
    ['namespace', 'deployment'],
    buckets=HEALTH_CHECK_BUCKETS
)
health_checks_total = Counter(
    'health_checks_total',
    'Deployment health checks by result',
    ['namespace', 'result']
)
webhook_requests_total = Counter(
    'webhook_requests_total',
    'Requests received on webhook endpoints',
    ['endpoint', 'code']
)
webhook_errors_total = Counter(
    'webhook_errors_total',
    'Webhook requests answered with an error status',
    ['endpoint', 'reason']
)
redis_connection_errors_total = Counter(
    'redis_connection_errors_total',
    'Redis calls that failed with a connection or timeout error'
)


def record_outcome(
    namespace: str,
    app: str,
    status: str,
    started_at: Optional[float],
    first_failure_at: Optional[float],
    finished_at: float
) -> None:
    """Record a deployment reaching success, failed or rolled_back"""
    deployments_total.labels(namespace=namespace, status=status).inc()
    
    if started_at is not None:
        deployment_duration_seconds.labels(
            namespace=namespace, status=status
        ).observe(max(finished_at - started_at, 0))
    
    outcome = {'success': 'retry_succeeded', 'rolled_back': 'rolled_back'}.get(status)
    if outcome and first_failure_at is not None:
        mana_recovery_duration_seconds.labels(
            namespace=namespace, app=app, outcome=outcome
        ).observe(max(finished_at - first_failure_at, 0))


def record_health_check(
    namespace: str,
    deployment: str,
    healthy: bool,
    duration_seconds: float
) -> None:
    health_check_duration_seconds.labels(
        namespace=namespace, deployment=deployment
    ).observe(duration_seconds)
    health_checks_total.labels(
        namespace=namespace, result='success' if healthy else 'failed'
    ).inc()


def record_webhook(endpoint: str, status_code: int) -> None:
    webhook_requests_total.labels(endpoint=endpoint, code=str(status_code)).inc()
    if status_code >= 400:
        reason = (
            'validation' if status_code == 422
            else 'server_error' if status_code >= 500
            else 'client_error'
        )
        webhook_errors_total.labels(endpoint=endpoint, reason=reason).inc()


class CountingRedis:
    """Delegate to a Redis client and count connection and timeout errors"""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def call(*args, **kwargs):
            try:
                return attribute(*args, **kwargs)
            except (redis.ConnectionError, redis.TimeoutError):
                redis_connection_errors_total.inc()
                raise

        return call


class ActiveDeploymentsCollector(Collector):
    """
    Report active_deployments from shared state at scrape time

    Every replica reports the same value, so queries must use max by
    (namespace), not sum. Namespaces seen before keep reporting 0 so graphs
    fall to zero instead of disappearing.
    """

    def __init__(self, source: Callable[[], Dict[str, int]]):
        self._source = source
        self._seen: set = set()

    def collect(self) -> Iterable[GaugeMetricFamily]:
        try:
            counts = self._source()
        except redis.RedisError as e:
            logger.warning(f"active_deployments unavailable: {e}")
            return

        self._seen.update(counts)
        family = GaugeMetricFamily(
            'active_deployments',
            'Deployments that have not reached a final status',
            labels=['namespace']
        )
        for namespace in sorted(self._seen):
            family.add_metric([namespace], counts.get(namespace, 0))
        yield family
