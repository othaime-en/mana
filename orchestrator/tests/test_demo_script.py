"""scripts/demo/simulate-failure.sh against stubbed kubectl, curl, docker and notify."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "demo" / "simulate-failure.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or not shutil.which("jq"), reason="script or jq not available"
)

KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$STUB_DIR/calls"
case "$*" in
  *"config current-context"*) echo "${STUB_CONTEXT:-kind-mana}" ;;
  *"jsonpath"*) echo "sample-app:1.0.0" ;;
  *"rollout status"*) exit 1 ;;
esac
"""

CURL = """#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  */metrics) printf 'mana_recovery_duration_seconds_sum{namespace="production"} 2.5\\nmana_recovery_duration_seconds_count{namespace="production"} 1\\n' ;;
  */deployment/*)
    index=$(cat "$STUB_DIR/polls" 2>/dev/null || echo 0)
    echo $((index + 1)) > "$STUB_DIR/polls"
    IFS=, read -ra states <<< "$STUB_STATES"
    last=$((${#states[@]} - 1))
    status="${states[$((index > last ? last : index))]}"
    printf '{"status":"%s","diagnosis":"CrashLoopBackOff","first_failure_at":100.0,"finished_at":102.5}' "$status"
    ;;
esac
"""

NOTIFY = """#!/usr/bin/env bash
echo "id=$MANA_DEPLOYMENT_ID ns=$MANA_NAMESPACE app=$MANA_APP_NAME version=$MANA_VERSION status=$MANA_STATUS failure=${MANA_FAILURE_TYPE:-}" >> "$STUB_DIR/notified"
"""

LOGGER = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_DIR/calls"
"""


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"kubectl": KUBECTL, "curl": CURL, "docker": LOGGER, "kind": LOGGER, "sleep": "#!/bin/sh\n"}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    notify = tmp_path / "notify.sh"
    notify.write_text(NOTIFY)
    notify.chmod(0o755)

    def invoke(*args, **overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MANA_", "STUB_", "ORCHESTRATOR"))}
        env.update(
            PATH=f"{bin_dir}:{env['PATH']}", STUB_DIR=str(tmp_path), NOTIFY_SCRIPT=str(notify),
            STUB_STATES="rolling_back,rolled_back",
            ORCHESTRATOR_URL="http://mana.test:8000/", MANA_WEBHOOK_SECRET="s" * 40, MANA_API_KEY="k" * 40,
        )
        env.update({k: v for k, v in overrides.items() if v is not None})
        for key in [k for k, v in overrides.items() if v is None]:
            env.pop(key, None)
        result = subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        return result, tmp_path

    return invoke


def lines(tmp, name):
    path = tmp / name
    return path.read_text().splitlines() if path.exists() else []


def test_help_documents_every_mode_and_option(run):
    result, _ = run("--help")

    assert result.returncode == 0
    for text in ("crash", "unhealthy", "unready", "oom", "--yes", "--kind-cluster", "ORCHESTRATOR_URL", "MANA_API_KEY"):
        assert text in result.stdout


@pytest.mark.parametrize("args,message", [
    ((), "a mode is required"),
    (("explode", "--yes"), "unknown mode: explode"),
    (("crash",), "--yes is required"),
    (("crash", "--yes", "--bogus"), "unknown option: --bogus"),
    (("crash", "--yes", "--timeout"), "--timeout needs a value"),
    (("crash", "--yes", "--timeout", "soon"), "whole number"),
    (("crash", "--yes", "--failure-type", "fire"), "unsupported failure type: fire"),
    (("crash", "oom", "--yes"), "only one mode"),
])
def test_bad_usage_exits_with_2_and_touches_nothing(run, args, message):
    result, tmp = run(*args)

    assert result.returncode == 2
    assert message in result.stderr
    assert lines(tmp, "calls") == [] and lines(tmp, "notified") == []


@pytest.mark.parametrize("missing", ["ORCHESTRATOR_URL", "MANA_WEBHOOK_SECRET", "MANA_API_KEY"])
def test_required_environment_is_checked_first(run, missing):
    result, tmp = run("crash", "--yes", **{missing: None})

    assert result.returncode == 1
    assert f"{missing} is required" in result.stderr
    assert lines(tmp, "notified") == []


def test_it_refuses_a_context_that_is_not_kind(run):
    result, tmp = run("crash", "--yes", STUB_CONTEXT="prod-eks")

    assert result.returncode == 1
    assert "not a kind cluster" in result.stderr
    assert not any("patch" in call for call in lines(tmp, "calls"))
    assert lines(tmp, "notified") == []


def test_any_context_lifts_the_kind_restriction(run):
    result, _ = run("crash", "--yes", "--any-context", STUB_CONTEXT="minikube")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode,variable", [
    ("crash", {"name": "FAIL_ON_START", "value": "true"}),
    ("unhealthy", {"name": "FAIL_HEALTH_AFTER", "value": "5"}),
    ("unready", {"name": "READY_NEVER", "value": "true"}),
    ("oom", {"name": "LEAK_MB_PER_S", "value": "50"}),
])
def test_each_mode_patches_the_image_and_switches_one_fault_on(run, mode, variable):
    result, tmp = run(mode, "--yes")

    assert result.returncode == 0, result.stderr
    (patch_call,) = [c for c in lines(tmp, "calls") if " patch deployment " in c]
    patch = json.loads(patch_call.split("--patch ", 1)[1])
    container = patch["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "sample-app"
    assert container["image"].startswith(f"sample-app:1.0.0-{mode}-")
    assert container["env"] == [{"name": "ENABLE_FAULT_INJECTION", "value": "true"}, variable]


def test_the_good_version_is_recorded_before_the_bad_one_fails(run):
    result, tmp = run("unhealthy", "--yes", "--namespace", "staging", "--app", "api", "--good-version", "2.1.0")

    assert result.returncode == 0, result.stderr
    good, started, failed = lines(tmp, "notified")
    assert "version=2.1.0 status=success" in good and "ns=staging app=api" in good
    assert "status=in_progress" in started and "version=2.1.0-unhealthy-" in started
    assert "status=failed failure=health_check_failure" in failed
    assert started.split()[0] == failed.split()[0]


def test_the_failure_type_can_be_chosen(run):
    _, tmp = run("crash", "--yes", "--failure-type", "deployment_failure")

    assert "failure=deployment_failure" in lines(tmp, "notified")[-1]


def test_it_polls_until_the_rollback_settles_and_reports_the_recovery_time(run):
    result, tmp = run("crash", "--yes", STUB_STATES="rolling_back,rolling_back,rolled_back")

    assert result.returncode == 0, result.stderr
    assert "Outcome:        rolled_back" in result.stdout
    assert "Recovery time:  2.5 s" in result.stdout
    assert "Diagnosis:      CrashLoopBackOff" in result.stdout
    assert "Live image:     sample-app:1.0.0" in result.stdout
    assert "mana_recovery_duration_seconds_count" in result.stdout
    assert int((tmp / "polls").read_text()) >= 3


def test_a_deployment_that_ends_failed_is_an_error(run):
    result, _ = run("crash", "--yes", STUB_STATES="failed")

    assert result.returncode == 1
    assert "was not rolled back (status: failed)" in result.stderr


def test_it_gives_up_when_nothing_settles_in_time(run):
    result, _ = run("crash", "--yes", "--timeout", "0", STUB_STATES="rolling_back")

    assert result.returncode == 1
    assert "no outcome after 0s" in result.stderr


def test_the_image_is_loaded_into_kind_only_when_asked(run):
    _, tmp = run("crash", "--yes")
    plain = lines(tmp, "calls")
    (tmp / "calls").unlink()

    run("crash", "--yes", "--kind-cluster", "mana-demo", "--image-repo", "ghcr.io/o/sample-app")
    calls = lines(tmp, "calls")

    assert not any(c.startswith(("docker", "kind")) for c in plain)
    assert any(c.startswith("docker tag ghcr.io/o/sample-app:1.0.0 ghcr.io/o/sample-app:1.0.0-crash-") for c in calls)
    assert any(
        c.startswith("kind load docker-image ghcr.io/o/sample-app:1.0.0-crash-") and "--name mana-demo" in c
        for c in calls
    )


def test_make_demo_runs_the_script_and_requires_a_mode():
    makefile = (SCRIPT.parents[2] / "Makefile").read_text()

    assert "./scripts/demo/simulate-failure.sh $(MODE) --yes --namespace $(NAMESPACE) $(DEMO_ARGS)" in makefile
    assert 'test -n "$(MODE)"' in makefile
    assert SCRIPT.stat().st_mode & 0o111
