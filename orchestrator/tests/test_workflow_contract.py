"""Static checks on the GitHub Actions workflows that feed the webhook."""

import re
from pathlib import Path

import pytest

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

pytestmark = pytest.mark.skipif(
    not WORKFLOWS_DIR.is_dir(), reason="workflows are not available in this checkout"
)


def workflow_text(name: str) -> str:
    return (WORKFLOWS_DIR / name).read_text()


def all_workflow_texts() -> dict[str, str]:
    return {path.name: path.read_text() for path in WORKFLOWS_DIR.glob("*.yml")}


@pytest.mark.parametrize("token", ["uuidgen", "date +%s"])
def test_no_workflow_derives_identity_from_a_value_that_changes_per_rerun(token):
    offenders = [name for name, text in all_workflow_texts().items() if token in text]
    assert offenders == []


def test_deploy_workflow_derives_deployment_id_from_run_id_and_namespace():
    text = workflow_text("deploy-environment.yml")
    assert re.search(r"deployment_id=deploy-\$\{GITHUB_RUN_ID\}-\$\{NAMESPACE\}", text)


def test_deploy_workflow_no_longer_accepts_a_caller_supplied_deployment_id():
    assert "deployment-id" not in workflow_text("deploy-environment.yml")
    assert "deployment-id" not in workflow_text("main-pipeline.yml")


@pytest.mark.parametrize("name", ["deploy-environment.yml", "build-and-test.yml"])
def test_webhook_payloads_report_run_attempt(name):
    assert "run_attempt" in workflow_text(name)


STEP_OUTPUT = r'echo "{name}=([a-z_]+)"'
PAYLOAD_FIELD = r'\\"{name}\\": \\"([a-z_]+)\\"'


def emitted(pattern: str, *file_names: str) -> set[str]:
    return {
        match
        for name in file_names
        for match in re.findall(pattern, workflow_text(name))
    }


def emitted_failure_types() -> set[str]:
    deploy = emitted(STEP_OUTPUT.format(name="failure_type"), "deploy-environment.yml")
    build = set(re.findall(r'FAILURE_TYPE="([a-z_]+)"', workflow_text("build-and-test.yml")))
    return deploy | build


def emitted_statuses() -> set[str]:
    return emitted(
        STEP_OUTPUT.format(name="status"), "deploy-environment.yml"
    ) | emitted(
        PAYLOAD_FIELD.format(name="status"), "deploy-environment.yml", "build-and-test.yml"
    )


def payload(status: str, failure_type: str | None) -> dict:
    return {
        "deployment_id": "deploy-1-production",
        "namespace": "production",
        "app_name": "sample-app",
        "version": "v1a2b3c4",
        "status": status,
        "failure_type": failure_type,
        "metadata": {"workflow_run_id": "1", "run_attempt": "1"},
    }


def test_workflows_emit_the_values_this_contract_covers():
    assert {"build_failure", "test_failure", "smoke_test_failure"} <= emitted_failure_types()
    assert {"in_progress", "success", "failed"} <= emitted_statuses()


def test_every_failure_type_a_workflow_can_send_is_accepted(api):
    for failure_type in emitted_failure_types():
        parsed = api.WebhookPayload.model_validate(payload("failed", failure_type))
        assert parsed.failure_type.value == failure_type


def test_every_status_a_workflow_can_send_is_accepted(api):
    for status in emitted_statuses():
        failure_type = "deployment_failure" if status == "failed" else ""
        parsed = api.WebhookPayload.model_validate(payload(status, failure_type))
        assert parsed.status.value == status


def test_orchestrator_is_the_only_rollback_owner():
    offenders = [
        name for name, text in all_workflow_texts().items() if "rollout undo" in text
    ]
    assert offenders == []
