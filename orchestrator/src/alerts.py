"""Tell people when automatic recovery needs them or has acted.

Delivery goes through the job queue, one job per channel, so an alert survives
a restart and a failing channel is retried without holding up recovery or
affecting the others. A channel that cannot be reached never changes the
outcome of a deployment.
"""

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import requests

from src import metrics
from src.jobs import JobQueue, PermanentJobError

logger = logging.getLogger(__name__)

JOB_NOTIFY = "notify"
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
SEVERITY_COLORS = {"warning": "#d99a00", "critical": "#c62828"}
STATUS_STATES = {"rolled_back": "failure"}


class AlertDeliveryError(Exception):
    """A channel did not accept the alert now but may accept it later"""


@dataclass(frozen=True)
class Alert:
    """What happened to a deployment and what, if anything, a human must do"""
    event: str
    severity: str
    title: str
    message: str
    deployment_id: str
    namespace: str
    app_name: str
    version: str
    previous_version: Optional[str] = None
    commit: Optional[str] = None
    failure_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def slack_text(value: Any) -> str:
    """Escape the characters Slack treats as markup so values cannot ping people"""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackChannel:
    name = "slack"

    def __init__(self, webhook_url: str):
        self._url = webhook_url

    def send(self, alert: Alert) -> str:
        fields = [
            ("Deployment", alert.deployment_id),
            ("Namespace", alert.namespace),
            ("App", alert.app_name),
            ("Version", alert.version),
        ]
        if alert.previous_version:
            fields.append(("Previous version", alert.previous_version))
        if alert.failure_type:
            fields.append(("Failure", alert.failure_type))
        payload = {
            "text": f"{slack_text(alert.title)}",
            "attachments": [{
                "color": SEVERITY_COLORS.get(alert.severity, "#808080"),
                "text": slack_text(alert.message),
                "fields": [
                    {"title": title, "value": slack_text(value), "short": True}
                    for title, value in fields
                ],
            }],
        }
        try:
            response = requests.post(self._url, json=payload, timeout=10)
        except requests.exceptions.RequestException as error:
            raise AlertDeliveryError(f"Slack request failed: {type(error).__name__}") from None
        if response.status_code == 200:
            return "sent"
        if response.status_code == 429 or response.status_code >= 500:
            raise AlertDeliveryError(f"Slack answered {response.status_code}")
        raise PermanentJobError(f"Slack refused the alert with {response.status_code}")


class GitHubStatusChannel:
    """Marks the commit with a mana/recovery status, visible on the commit and its PR"""
    name = "github_status"

    def __init__(self, token: str, repo: str):
        self._token = token
        self._repo = repo

    def send(self, alert: Alert) -> str:
        if not alert.commit or not COMMIT_PATTERN.match(alert.commit):
            return "skipped"
        state = STATUS_STATES.get(alert.event, "error")
        url = f"https://api.github.com/repos/{self._repo}/statuses/{alert.commit}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = {"state": state, "context": "mana/recovery", "description": alert.title[:140]}
        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
        except requests.exceptions.RequestException as error:
            metrics.github_api_requests_total.labels(endpoint="status", code="error").inc()
            raise AlertDeliveryError(f"GitHub request failed: {type(error).__name__}") from None
        metrics.github_api_requests_total.labels(endpoint="status", code=str(response.status_code)).inc()
        if response.status_code == 201:
            return "sent"
        if response.status_code in (403, 429) or response.status_code >= 500:
            raise AlertDeliveryError(f"GitHub answered {response.status_code}")
        raise PermanentJobError(f"GitHub refused the status with {response.status_code}")


class Alerter:
    """Queues an alert for every configured channel and delivers queued ones"""

    def __init__(self, queue: JobQueue, channels: List[Any]):
        self._queue = queue
        self._channels = {channel.name: channel for channel in channels}

    @property
    def channel_names(self) -> List[str]:
        return sorted(self._channels)

    def dispatch(self, alert: Alert, retry_count: int = 0) -> None:
        """Queue the alert; a failure to queue is logged, never raised"""
        for name in self._channels:
            try:
                self._queue.enqueue(
                    JOB_NOTIFY,
                    {"channel": name, "alert": alert.to_dict()},
                    0,
                    f"{JOB_NOTIFY}:{name}:{alert.deployment_id}:{alert.event}:{retry_count}",
                )
            except Exception:
                logger.exception("Could not queue the %s alert for %s", name, alert.deployment_id)

    def deliver(self, channel_name: str, payload: Dict[str, Any]) -> str:
        """Send one queued alert; raises when the channel should be retried"""
        channel = self._channels.get(channel_name)
        if channel is None:
            raise PermanentJobError(f"alert channel {channel_name} is no longer configured")
        try:
            outcome = channel.send(Alert(**payload))
        except Exception:
            metrics.alerts_sent_total.labels(channel=channel_name, outcome="failed").inc()
            raise
        metrics.alerts_sent_total.labels(channel=channel_name, outcome=outcome).inc()
        return outcome
