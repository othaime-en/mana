import time
from unittest.mock import Mock, patch

import pytest
import requests

from src import metrics
from src.alerts import (
    Alert,
    AlertDeliveryError,
    Alerter,
    GitHubStatusChannel,
    SlackChannel,
    slack_text,
)
from src.config import OrchestratorConfig
from src.jobs import Job, PermanentJobError
from src.orchestrator import (
    DeploymentState,
    DeploymentStatus,
    FailureType,
    SelfHealingOrchestrator,
)
from tests.fakes import FakeJobQueue, FakeLocks, FakeRedis

SHA = "a" * 40


def make_alert(**overrides):
    values = dict(
        event="rolled_back", severity="warning", title="sample-app v2 was rolled back to v1",
        message="Version v2 kept failing.", deployment_id="d1", namespace="production",
        app_name="sample-app", version="v2", previous_version="v1", commit=SHA,
        failure_type="health_check_failure",
    )
    values.update(overrides)
    return Alert(**values)


def count(channel, outcome):
    return metrics.alerts_sent_total.labels(channel=channel, outcome=outcome)._value.get()


class TestSlackChannel:
    def test_the_alert_is_posted_with_its_details(self):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=200)) as post:
            outcome = SlackChannel("https://hooks.slack.test/T/B/x").send(make_alert())

        assert outcome == "sent"
        assert post.call_args.args[0] == "https://hooks.slack.test/T/B/x"
        payload = post.call_args.kwargs["json"]
        assert "rolled back to v1" in payload["text"]
        fields = {f["title"]: f["value"] for f in payload["attachments"][0]["fields"]}
        assert fields["Deployment"] == "d1" and fields["Previous version"] == "v1"
        assert fields["Failure"] == "health_check_failure"
        assert payload["attachments"][0]["color"] == "#d99a00"

    def test_values_cannot_inject_slack_markup(self):
        assert slack_text("<!channel> a&b") == "&lt;!channel&gt; a&amp;b"
        with patch("src.alerts.requests.post", return_value=Mock(status_code=200)) as post:
            SlackChannel("https://x").send(make_alert(message="<!channel> now", deployment_id="<@U1>"))

        sent = post.call_args.kwargs["json"]["attachments"][0]
        assert "<!channel>" not in sent["text"]
        assert all("<@U1>" not in f["value"] for f in sent["fields"])

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_server_side_trouble_is_retryable(self, code):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=code)):
            with pytest.raises(AlertDeliveryError, match=str(code)):
                SlackChannel("https://x").send(make_alert())

    @pytest.mark.parametrize("code", [400, 403, 404])
    def test_a_rejected_alert_is_permanent(self, code):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=code)):
            with pytest.raises(PermanentJobError):
                SlackChannel("https://x").send(make_alert())

    def test_network_errors_are_retryable_and_never_leak_the_url(self):
        url = "https://hooks.slack.test/T/SECRET/token"
        with patch("src.alerts.requests.post", side_effect=requests.exceptions.ConnectionError(f"failed {url}")):
            with pytest.raises(AlertDeliveryError) as raised:
                SlackChannel(url).send(make_alert())

        assert "SECRET" not in str(raised.value) and "token" not in str(raised.value)


class TestGitHubStatusChannel:
    def channel(self):
        return GitHubStatusChannel("tok", "owner/repo")

    @pytest.mark.parametrize("event,state", [
        ("rolled_back", "failure"), ("rollback_failed", "error"),
        ("manual_intervention", "error"), ("job_abandoned", "error"),
    ])
    def test_the_commit_is_marked_by_event(self, event, state):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=201)) as post:
            outcome = self.channel().send(make_alert(event=event))

        assert outcome == "sent"
        assert post.call_args.args[0] == f"https://api.github.com/repos/owner/repo/statuses/{SHA}"
        assert post.call_args.kwargs["json"]["state"] == state
        assert post.call_args.kwargs["json"]["context"] == "mana/recovery"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_the_description_fits_githubs_limit(self):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=201)) as post:
            self.channel().send(make_alert(title="x" * 400))

        assert len(post.call_args.kwargs["json"]["description"]) == 140

    @pytest.mark.parametrize("commit", [None, "", "not-a-sha", "../../repos", "abc"])
    def test_without_a_valid_commit_nothing_is_sent(self, commit):
        with patch("src.alerts.requests.post") as post:
            outcome = self.channel().send(make_alert(commit=commit))

        assert outcome == "skipped"
        post.assert_not_called()

    @pytest.mark.parametrize("code", [403, 429, 502])
    def test_trouble_that_may_clear_up_is_retryable(self, code):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=code)):
            with pytest.raises(AlertDeliveryError):
                self.channel().send(make_alert())

    @pytest.mark.parametrize("code", [401, 404, 422])
    def test_other_refusals_are_permanent(self, code):
        with patch("src.alerts.requests.post", return_value=Mock(status_code=code)):
            with pytest.raises(PermanentJobError):
                self.channel().send(make_alert())

    def test_requests_are_counted_for_the_dashboard(self):
        before = metrics.github_api_requests_total.labels(endpoint="status", code="201")._value.get()
        with patch("src.alerts.requests.post", return_value=Mock(status_code=201)):
            self.channel().send(make_alert())

        assert metrics.github_api_requests_total.labels(endpoint="status", code="201")._value.get() == before + 1


class RecordingChannel:
    def __init__(self, name, result="sent", error=None):
        self.name, self.result, self.error, self.sent = name, result, error, []

    def send(self, alert):
        if self.error:
            raise self.error
        self.sent.append(alert)
        return self.result


class TestAlerter:
    def test_one_job_is_queued_per_channel(self):
        queue = FakeJobQueue()
        Alerter(queue, [RecordingChannel("slack"), RecordingChannel("github_status")]).dispatch(make_alert())

        assert sorted(j["payload"]["channel"] for j in queue.jobs) == ["github_status", "slack"]
        assert {j["kind"] for j in queue.jobs} == {"notify"}

    def test_the_same_alert_is_queued_once(self):
        queue = FakeJobQueue()
        alerter = Alerter(queue, [RecordingChannel("slack")])
        alerter.dispatch(make_alert(), retry_count=4)
        alerter.dispatch(make_alert(), retry_count=4)

        assert len(queue.jobs) == 1

    def test_without_channels_nothing_is_queued(self):
        queue = FakeJobQueue()
        Alerter(queue, []).dispatch(make_alert())

        assert queue.jobs == []

    def test_a_queueing_failure_is_swallowed(self):
        queue = Mock()
        queue.enqueue.side_effect = RuntimeError("redis down")

        Alerter(queue, [RecordingChannel("slack")]).dispatch(make_alert())

    def test_delivery_counts_the_outcome(self):
        channel = RecordingChannel("slack")
        before = count("slack", "sent")

        outcome = Alerter(FakeJobQueue(), [channel]).deliver("slack", make_alert().to_dict())

        assert outcome == "sent" and count("slack", "sent") == before + 1
        assert channel.sent == [make_alert()]

    def test_skipped_alerts_are_counted_separately(self):
        before = count("slack", "skipped")
        Alerter(FakeJobQueue(), [RecordingChannel("slack", result="skipped")]).deliver("slack", make_alert().to_dict())

        assert count("slack", "skipped") == before + 1

    def test_a_failing_channel_raises_so_the_job_retries(self):
        before = count("slack", "failed")
        alerter = Alerter(FakeJobQueue(), [RecordingChannel("slack", error=AlertDeliveryError("503"))])

        with pytest.raises(AlertDeliveryError):
            alerter.deliver("slack", make_alert().to_dict())

        assert count("slack", "failed") == before + 1

    def test_a_removed_channel_is_a_permanent_error(self):
        with pytest.raises(PermanentJobError, match="no longer configured"):
            Alerter(FakeJobQueue(), []).deliver("slack", make_alert().to_dict())


@pytest.fixture
def orchestrator():
    with patch("src.orchestrator.redis.Redis"), \
         patch("src.orchestrator.k8s_config.load_kube_config"), \
         patch("src.orchestrator.client.AppsV1Api"), \
         patch("src.orchestrator.client.CoreV1Api"):
        orch = SelfHealingOrchestrator(use_config=False)
    orch.redis_client = FakeRedis()
    orch.jobs = FakeJobQueue()
    orch.locks = FakeLocks()
    orch.alerter = Alerter(orch.jobs, [RecordingChannel("slack")])
    orch.max_retries = 1
    orch.get_previous_version = Mock(return_value="v1")
    return orch


def alerts(orch):
    return [j["payload"]["alert"] for j in orch.jobs.of_kind("notify")]


def stored_state(orch, status=DeploymentStatus.ROLLING_BACK):
    state = DeploymentState(
        deployment_id="d1", namespace="production", app_name="sample-app", version="v2",
        status=status, previous_version="v1", retry_count=2,
        failure_type=FailureType.HEALTH_CHECK_FAILURE, timestamp=time.time(),
        metadata={"commit": SHA}, started_at=time.time() - 9, first_failure_at=time.time() - 5,
    )
    orch.save_deployment_state(state)
    return state


def rollback_job():
    return Job("rollback:d1:2", "rollback", {
        "deployment_id": "d1", "namespace": "production", "deployment_name": "sample-app",
        "version": "v2", "target_version": "v1",
    }, attempts=1)


class TestAlertsFromTheOrchestrator:
    def run_rollback(self, orch, result):
        with patch.object(orch, "get_live_version", return_value="v2"), \
             patch.object(orch, "rollback_deployment", return_value=result):
            orch.job_handlers()["rollback"](rollback_job())

    def test_a_completed_rollback_is_announced(self, orchestrator):
        stored_state(orchestrator)
        self.run_rollback(orchestrator, True)

        (alert,) = alerts(orchestrator)
        assert (alert["event"], alert["severity"]) == ("rolled_back", "warning")
        assert "rolled back to v1" in alert["title"]
        assert alert["commit"] == SHA and alert["previous_version"] == "v1"

    def test_a_failed_rollback_is_critical(self, orchestrator):
        stored_state(orchestrator)
        self.run_rollback(orchestrator, False)

        (alert,) = alerts(orchestrator)
        assert (alert["event"], alert["severity"]) == ("rollback_failed", "critical")

    def test_an_abandoned_job_is_critical_and_fails_the_deployment(self, orchestrator):
        stored_state(orchestrator)

        orchestrator.handle_dead_job(rollback_job(), RuntimeError("api down"))

        (alert,) = alerts(orchestrator)
        assert alert["event"] == "job_abandoned" and "api down" in alert["message"]

    def test_an_out_of_date_dead_job_stays_silent(self, orchestrator):
        stored_state(orchestrator, DeploymentStatus.ROLLED_BACK)

        orchestrator.handle_dead_job(rollback_job(), RuntimeError("late"))

        assert alerts(orchestrator) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure,metadata,previous,message", [
        (FailureType.TEST_FAILURE, {"workflow_run_id": "9"}, "v1", "nothing was deployed"),
        (FailureType.DEPLOYMENT_FAILURE, {}, "v1", "workflow_run_id"),
    ])
    async def test_every_alert_decision_notifies(self, orchestrator, failure, metadata, previous, message):
        orchestrator.max_retries = 0 if metadata else 1
        orchestrator.get_previous_version = Mock(return_value=previous)

        result = await orchestrator.handle_deployment_failure(
            "d1", "production", "sample-app", "v2", failure, metadata
        )

        assert result["action"] == "alert"
        (alert,) = alerts(orchestrator)
        assert alert["event"] == "manual_intervention" and alert["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_no_rollback_target_notifies(self, orchestrator):
        orchestrator.max_retries = 0
        orchestrator.get_previous_version = Mock(return_value=None)

        result = await orchestrator.handle_deployment_failure(
            "d1", "production", "sample-app", "v2", FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "9"}
        )

        assert result["action"] == "alert"
        assert "nothing to roll back to" in alerts(orchestrator)[0]["message"]

    @pytest.mark.asyncio
    async def test_retries_and_skips_do_not_notify(self, orchestrator):
        await orchestrator.handle_deployment_failure(
            "d1", "production", "sample-app", "v2", FailureType.DEPLOYMENT_FAILURE, {"workflow_run_id": "9"}
        )

        assert alerts(orchestrator) == []

    def test_the_notify_job_delivers_and_audits(self, orchestrator):
        channel = RecordingChannel("slack")
        orchestrator.alerter = Alerter(orchestrator.jobs, [channel])
        job = Job("n", "notify", {"channel": "slack", "alert": make_alert().to_dict()}, attempts=1)

        with patch("src.orchestrator.audit_logger") as audit:
            orchestrator.job_handlers()["notify"](job)

        assert channel.sent == [make_alert()]
        assert audit.log_alert_sent.call_args.kwargs["channel"] == "slack"

    def test_a_skipped_alert_is_not_audited_as_sent(self, orchestrator):
        orchestrator.alerter = Alerter(orchestrator.jobs, [RecordingChannel("slack", result="skipped")])
        job = Job("n", "notify", {"channel": "slack", "alert": make_alert().to_dict()}, attempts=1)

        with patch("src.orchestrator.audit_logger") as audit:
            orchestrator.job_handlers()["notify"](job)

        audit.log_alert_sent.assert_not_called()

    def test_a_dead_alert_neither_alerts_again_nor_touches_state(self, orchestrator):
        stored_state(orchestrator)
        job = Job("n", "notify", {"channel": "slack", "alert": make_alert().to_dict()}, attempts=5)

        orchestrator.handle_dead_job(job, RuntimeError("slack down"))

        assert alerts(orchestrator) == []
        assert orchestrator.get_deployment_state("d1").status is DeploymentStatus.ROLLING_BACK


class TestChannelConfiguration:
    def build(self, monkeypatch, **env):
        for name in ("SLACK_WEBHOOK_URL", "ALERT_GITHUB_STATUS", "GITHUB_TOKEN", "GITHUB_REPO"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("REDIS_HOST", "x")
        config = OrchestratorConfig.from_env()
        config.webhook_secrets = ("w" * 40,)
        config.api_keys = ("k" * 40,)
        with patch("src.orchestrator.redis.Redis"), \
             patch("src.orchestrator.get_config", return_value=config), \
             patch("src.orchestrator.k8s_config.load_kube_config"), \
             patch("src.orchestrator.client.AppsV1Api"), \
             patch("src.orchestrator.client.CoreV1Api"):
            return SelfHealingOrchestrator(use_config=True).alerter.channel_names

    def test_no_channels_by_default(self, monkeypatch):
        assert self.build(monkeypatch) == []

    def test_slack_needs_its_url(self, monkeypatch):
        assert self.build(monkeypatch, SLACK_WEBHOOK_URL="https://hooks.slack.test/x") == ["slack"]

    def test_commit_status_needs_the_github_settings(self, monkeypatch):
        assert self.build(monkeypatch, GITHUB_TOKEN="t", GITHUB_REPO="o/r") == ["github_status"]

    def test_commit_status_can_be_switched_off(self, monkeypatch):
        assert self.build(monkeypatch, GITHUB_TOKEN="t", GITHUB_REPO="o/r", ALERT_GITHUB_STATUS="false") == []

    def test_a_plain_http_slack_url_is_rejected(self, monkeypatch):
        with pytest.raises(ValueError, match="https"):
            self.build(monkeypatch, SLACK_WEBHOOK_URL="http://hooks.slack.test/x")

    def test_the_webhook_url_is_not_in_the_config_repr(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/T/SECRET")

        assert "SECRET" not in repr(OrchestratorConfig.from_env())
