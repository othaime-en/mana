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
