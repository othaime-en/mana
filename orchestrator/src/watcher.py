"""Detect failed rollouts from the cluster, without a webhook.

A webhook can be lost, the runner can die, and a deployment made with kubectl,
Helm or Argo CD sends none. The watcher lists the Deployments that opted in with
the label mana.io/managed=true, decides from their own status whether a rollout
is stuck, and hands each one to the same recovery policy a failed webhook goes
through.

It reconciles instead of watching: a periodic list needs only the `list`
permission, survives restarts and missed events, and every decision comes from
the object as it is now. All replicas may run it. A deterministic deployment id
and a short Redis claim let exactly one of them act on a given rollout.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import redis

from src import metrics
from src.orchestrator import (
    ContainerNotFoundError,
    DeploymentStatus,
    FailureType,
    find_container,
)
from src.utils import split_image_reference

logger = logging.getLogger(__name__)

MANAGED_LABEL = "mana.io/managed"
MANAGED_SELECTOR = f"{MANAGED_LABEL}=true"
ROLLING_REASONS = frozenset({"ReplicaSetUpdated", "NewReplicaSetCreated", "FoundNewReplicaSet"})
HANDLED_STATUSES = frozenset({
    DeploymentStatus.RETRY_SCHEDULED,
    DeploymentStatus.ROLLING_BACK,
    DeploymentStatus.FAILED,
    DeploymentStatus.ROLLED_BACK,
})
CLAIM_PREFIX = "mana:watch:claim"
CLAIM_SECONDS = 60
RECENT_STATES_CHECKED = 50

DEADLINE_EXCEEDED = "progress_deadline_exceeded"
NO_PROGRESS = "no_progress"


@dataclass(frozen=True)
class StuckRollout:
    """A rollout the cluster has stopped making progress on"""
    namespace: str
    app: str
    version: str
    generation: int
    reason: str
    detail: str

    @property
    def deployment_id(self) -> str:
        """Stable for one rollout (a new spec is a new generation), so it is handled once"""
        return f"watch-{self.namespace}-{self.app}-g{self.generation}"


def assess_rollout(deployment, now: float, stuck_seconds: float) -> Optional[Tuple[str, str]]:
    """
    Say whether a Deployment's rollout is stuck, as (reason, detail), else None

    Two signals. The controller gave up: Progressing is False with reason
    ProgressDeadlineExceeded. Or the rollout is still going, is not complete,
    and the controller has recorded no progress for stuck_seconds, which acts
    before the progress deadline (600 s by default) does. A finished rollout,
    even one that later lost a pod, is never stuck: that is degradation, not a
    failed rollout.
    """
    status = deployment.status
    conditions = {c.type: c for c in (status.conditions or [])} if status else {}

    progressing = conditions.get("Progressing")
    if progressing and progressing.status == "False" and progressing.reason == "ProgressDeadlineExceeded":
        return DEADLINE_EXCEEDED, progressing.message or "the progress deadline passed"

    if not progressing or progressing.status != "True" or progressing.reason not in ROLLING_REASONS:
        return None

    desired = deployment.spec.replicas if deployment.spec.replicas is not None else 1
    generation = deployment.metadata.generation or 0
    observed = status.observed_generation or 0
    updated = status.updated_replicas or 0
    total = status.replicas or 0
    ready = status.ready_replicas or 0
    incomplete = observed < generation or updated < desired or total > updated or ready < desired
    if not incomplete:
        return None

    last_progress = progressing.last_update_time
    if last_progress is None:
        return None
    idle = now - last_progress.timestamp()
    if idle < stuck_seconds:
        return None
    return NO_PROGRESS, f"{ready}/{desired} replicas ready and no progress for {int(idle)} s"


def image_version(reference) -> str:
    """
    The label a rollout's image goes by: its tag, else a short digest, else "latest"

    An image pinned by digest or without a tag cannot be matched to a rollback
    target, so the policy turns a rollback for it into an alert instead of
    skipping the rollout; the label still names it.
    """
    if reference.tag:
        return reference.tag
    if reference.digest:
        return reference.digest[:19]
    return "latest"


def stuck_rollout(deployment, namespace: str, now: float, stuck_seconds: float) -> Optional[StuckRollout]:
    """The StuckRollout for a managed Deployment, or None when it is fine or cannot be read"""
    assessment = assess_rollout(deployment, now, stuck_seconds)
    if assessment is None:
        return None
    name = deployment.metadata.name
    try:
        reference = split_image_reference(find_container(deployment, name).image)
    except ContainerNotFoundError as error:
        logger.warning(f"Cannot tell which version {namespace}/{name} runs: {error}")
        return None
    reason, detail = assessment
    return StuckRollout(
        namespace=namespace,
        app=name,
        version=image_version(reference),
        generation=deployment.metadata.generation or 0,
        reason=reason,
        detail=detail,
    )


class Watcher:
    """Periodically finds stuck rollouts and sends them through the recovery policy"""

    def __init__(
        self,
        orchestrator,
        namespaces: List[str],
        interval_seconds: float,
        stuck_seconds: float,
    ):
        self._orchestrator = orchestrator
        self._namespaces = namespaces
        self._interval = interval_seconds
        self._stuck_seconds = stuck_seconds
        self._stop = asyncio.Event()

    def find_stuck(self) -> List[StuckRollout]:
        """List the managed Deployments and return the stuck rollouts (blocking calls)"""
        found: List[StuckRollout] = []
        now = time.time()
        for namespace in self._namespaces:
            try:
                listing = self._orchestrator.k8s_apps.list_namespaced_deployment(
                    namespace=namespace, label_selector=MANAGED_SELECTOR
                )
            except Exception as error:  # noqa: BLE001 - one namespace must not stop the others
                metrics.watcher_scans_total.labels(outcome="error").inc()
                logger.error(f"Watcher cannot list deployments in {namespace}: {error}")
                continue
            metrics.watcher_scans_total.labels(outcome="ok").inc()
            for deployment in listing.items:
                stuck = stuck_rollout(deployment, namespace, now, self._stuck_seconds)
                if stuck:
                    found.append(stuck)
        return found

    def _already_handled(self, stuck: StuckRollout) -> bool:
        """Whether a webhook, an earlier scan or another replica already dealt with this rollout"""
        if self._orchestrator.get_deployment_state(stuck.deployment_id) is not None:
            return True
        recent = self._orchestrator.get_recent_deployments(
            namespace=stuck.namespace, limit=RECENT_STATES_CHECKED
        )
        return any(
            state.app_name == stuck.app
            and state.version == stuck.version
            and state.status in HANDLED_STATUSES
            for state in recent
        )

    def _claim(self, stuck: StuckRollout) -> bool:
        """Win the right to act on this rollout; one replica per claim window"""
        return bool(self._orchestrator.redis_client.set(
            f"{CLAIM_PREFIX}:{stuck.deployment_id}", "1", nx=True, ex=CLAIM_SECONDS
        ))

    async def handle(self, stuck: StuckRollout) -> bool:
        """Run one stuck rollout through the policy; False when it was not ours to handle"""
        try:
            handled = await asyncio.to_thread(self._already_handled, stuck)
            if handled or not await asyncio.to_thread(self._claim, stuck):
                return False
        except redis.RedisError as error:
            logger.error(f"Watcher cannot reach Redis for {stuck.deployment_id}: {error}")
            return False

        logger.warning(
            f"Stuck rollout of {stuck.app} {stuck.version} in {stuck.namespace}: {stuck.detail}"
        )
        metrics.watcher_detections_total.labels(namespace=stuck.namespace, reason=stuck.reason).inc()
        await self._orchestrator.handle_deployment_failure(
            deployment_id=stuck.deployment_id,
            namespace=stuck.namespace,
            deployment_name=stuck.app,
            version=stuck.version,
            failure_type=FailureType.ROLLOUT_STUCK,
            metadata={
                "detected_by": "watcher",
                "detection": stuck.reason,
                "generation": str(stuck.generation),
            },
        )
        return True

    async def scan_once(self) -> int:
        """One reconcile pass; returns how many rollouts were handed to the policy"""
        handled = 0
        for stuck in await asyncio.to_thread(self.find_stuck):
            try:
                handled += await self.handle(stuck)
            except Exception:  # noqa: BLE001 - keep watching after one bad rollout
                logger.exception(f"Watcher failed to handle {stuck.deployment_id}")
        return handled

    async def run(self) -> None:
        """Scan until stop() is called"""
        logger.info(
            f"Watcher scanning {self._namespaces} every {self._interval}s "
            f"(stuck after {self._stuck_seconds}s without progress)"
        )
        while not self._stop.is_set():
            await self.scan_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

