"""Work out why a rollout failed from the signals the cluster already holds.

The functions here only read objects the Kubernetes client returned, so they
need no cluster to test. A transient cause (the image is not pullable yet, no
node has room) may be fixed by another attempt. A deterministic one (the
container crashes, runs out of memory, has a broken config) will fail again
the same way, so retrying it only delays recovery.
"""

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

from src.utils import split_image_reference

logger = logging.getLogger(__name__)

TRANSIENT_REASONS = frozenset({"ImagePullBackOff", "ErrImagePull", "Unschedulable"})
DETERMINISTIC_REASONS = frozenset({
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
    "RunContainerError",
    "OOMKilled",
    "ProgressDeadlineExceeded",
})
KNOWN_REASONS = TRANSIENT_REASONS | DETERMINISTIC_REASONS
OTHER_REASON = "other"


@dataclass(frozen=True)
class Diagnosis:
    """Why a rollout failed, and whether trying again could change that"""
    reason: str
    transient: bool
    detail: str = ""

    @property
    def metric_reason(self) -> str:
        """The reason as a bounded metric label"""
        return self.reason if self.reason in KNOWN_REASONS else OTHER_REASON


def _classify(reason: str, detail: str = "") -> Diagnosis:
    return Diagnosis(reason=reason, transient=reason in TRANSIENT_REASONS, detail=detail)


def _runs_version(pod, version: str) -> bool:
    """Whether any container of the pod uses the image tag of the failed version"""
    for container in (pod.spec.containers if pod.spec else None) or []:
        if split_image_reference(container.image or "").tag == version:
            return True
    return False


def _pod_findings(pod) -> List[Diagnosis]:
    found: List[Diagnosis] = []
    name = pod.metadata.name if pod.metadata else "pod"
    status = pod.status
    if status is None:
        return found

    for condition in status.conditions or []:
        if (
            condition.type == "PodScheduled"
            and condition.status == "False"
            and condition.reason == "Unschedulable"
        ):
            found.append(_classify("Unschedulable", f"{name}: {condition.message or ''}".strip()))

    for container in status.container_statuses or []:
        waiting = container.state.waiting if container.state else None
        if waiting and waiting.reason in KNOWN_REASONS:
            found.append(_classify(waiting.reason, f"{name}/{container.name}: {waiting.message or ''}".strip()))

        for terminated in (
            container.state.terminated if container.state else None,
            container.last_state.terminated if container.last_state else None,
        ):
            if terminated and terminated.reason == "OOMKilled":
                found.append(_classify("OOMKilled", f"{name}/{container.name} was killed for using too much memory"))
    return found


def diagnose_pods(pods: Iterable, version: Optional[str] = None) -> Optional[Diagnosis]:
    """
    Diagnose the pods of a failed rollout

    With a version, only pods running that image tag count, so healthy pods of
    the previous revision and their history cannot mislead the result. When
    pods disagree, a deterministic cause wins: one pod that cannot succeed is
    enough to stop retrying.
    """
    findings: List[Diagnosis] = []
    for pod in pods:
        if version is not None and not _runs_version(pod, version):
            continue
        findings.extend(_pod_findings(pod))

    for finding in findings:
        if not finding.transient:
            return finding
    return findings[0] if findings else None


def diagnose_deployment(deployment) -> Optional[Diagnosis]:
    """The Deployment controller giving up on the rollout (progress deadline passed)"""
    conditions = deployment.status.conditions if deployment.status else None
    for condition in conditions or []:
        if (
            condition.type == "Progressing"
            and condition.status == "False"
            and condition.reason == "ProgressDeadlineExceeded"
        ):
            return _classify("ProgressDeadlineExceeded", condition.message or "")
    return None


def diagnose(pods: Iterable, deployment, version: Optional[str] = None) -> Optional[Diagnosis]:
    """Pod signals first, because they name the cause; then the Deployment condition"""
    return diagnose_pods(pods, version) or diagnose_deployment(deployment)
