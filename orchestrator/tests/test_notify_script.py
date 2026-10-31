"""scripts/notify-orchestrator.sh against a stubbed curl, verified with the real verifier."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src import auth

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "notify-orchestrator.sh"
SECRET = "s" * 40

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or not all(shutil.which(t) for t in ("jq", "python3")),
    reason="script, jq or python3 not available",
)

CURL_STUB = """#!/usr/bin/env bash
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --data-binary) printf '%s' "${args[$((i + 1))]}" > "$STUB_DIR/body" ;;
    --header) echo "${args[$((i + 1))]}" >> "$STUB_DIR/headers" ;;
    --request) echo "${args[$((i + 2))]}" > "$STUB_DIR/url" ;;
  esac
done
echo "$*" > "$STUB_DIR/argv"
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def run(tmp_path):
    stub = tmp_path / "curl"
    stub.write_text(CURL_STUB)
    stub.chmod(0o755)

    def invoke(**overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MANA_", "GITHUB_"))}
        env.update(
            PATH=f"{tmp_path}:{env['PATH']}",
            STUB_DIR=str(tmp_path),
            ORCHESTRATOR_URL="https://mana.example.com/",
            MANA_WEBHOOK_SECRET=SECRET,
            MANA_DEPLOYMENT_ID="deploy-1-production",
            MANA_NAMESPACE="production",
            MANA_VERSION="v1a2b3c4",
            MANA_STATUS="in_progress",
            GITHUB_SHA="abc123",
            GITHUB_ACTOR="octocat",
            GITHUB_REF_NAME="main",
            GITHUB_RUN_ATTEMPT="2",
        )
        env.update({k: v for k, v in overrides.items() if v is not None})
        for key in [k for k, v in overrides.items() if v is None]:
            env.pop(key, None)
        result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
        return result, tmp_path

    return invoke


def sent(tmp):
    body = (tmp / "body").read_bytes()
    headers = dict(
        line.split(": ", 1) for line in (tmp / "headers").read_text().splitlines() if ": " in line
    )
    return body, headers


def test_payload_matches_the_webhook_contract(run, api):
    result, tmp = run(MANA_WORKFLOW_RUN_ID="42")
    assert result.returncode == 0, result.stderr
    body, _ = sent(tmp)
    payload = json.loads(body)
    assert payload["metadata"] == {
        "commit": "abc123", "author": "octocat", "branch": "main",
        "run_attempt": "2", "workflow_run_id": "42",
    }
    parsed = api.WebhookPayload.model_validate(payload)
    assert parsed.deployment_id == "deploy-1-production"
    assert parsed.failure_type is None


def test_signature_is_accepted_by_the_orchestrator_verifier(run):
    result, tmp = run()
    body, headers = sent(tmp)
    reason = auth.signature_failure(
        [SECRET], headers["X-Mana-Timestamp"], headers["X-Mana-Signature"], body
    )
    assert reason is None


def test_signature_is_rejected_for_another_secret(run):
    _, tmp = run()
    body, headers = sent(tmp)
    assert auth.signature_failure(
        ["x" * 40], headers["X-Mana-Timestamp"], headers["X-Mana-Signature"], body
    ) == "invalid"


def test_secret_never_appears_in_the_curl_arguments(run):
    _, tmp = run()
    assert SECRET not in (tmp / "argv").read_text()


def test_url_is_normalised_and_points_at_the_webhook(run):
    _, tmp = run()
    assert (tmp / "url").read_text().strip() == "https://mana.example.com/webhook/deployment"


def test_failed_status_carries_failure_type_and_is_valid(run, api):
    result, tmp = run(MANA_STATUS="failed", MANA_FAILURE_TYPE="health_check_failure")
    assert result.returncode == 0, result.stderr
    body, _ = sent(tmp)
    parsed = api.WebhookPayload.model_validate_json(body)
    assert parsed.failure_type.value == "health_check_failure"
    assert "workflow_run_id" not in parsed.metadata


def test_values_with_quotes_stay_valid_json(run):
    result, tmp = run(MANA_VERSION='v1"; rm -rf / #', GITHUB_ACTOR='o"ctocat')
    assert result.returncode == 0
    assert json.loads(sent(tmp)[0])["version"] == 'v1"; rm -rf / #'


def test_nothing_is_sent_without_an_orchestrator_url(run):
    result, tmp = run(ORCHESTRATOR_URL="")
    assert result.returncode == 0
    assert not (tmp / "argv").exists()


@pytest.mark.parametrize("overrides", [
    {"MANA_WEBHOOK_SECRET": None},
    {"MANA_STATUS": "exploded"},
    {"MANA_STATUS": "failed", "MANA_FAILURE_TYPE": ""},
    {"ORCHESTRATOR_URL": "ftp://mana"},
    {"MANA_DEPLOYMENT_ID": None},
])
def test_invalid_input_fails_without_sending(run, overrides):
    result, tmp = run(**overrides)
    assert result.returncode == 1
    assert not (tmp / "argv").exists()


def test_a_rejected_webhook_fails_the_script_with_an_annotation(run):
    result, _ = run(STUB_EXIT="22", GITHUB_ACTIONS="true")
    assert result.returncode == 1
    assert "::warning::Mana webhook not delivered" in result.stdout
