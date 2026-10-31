"""Behaviour of scripts/create-secrets.sh against a stubbed kubectl."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "create-secrets.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or shutil.which("openssl") is None,
    reason="script or openssl not available",
)

STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [[ "$*" == *"get secret"* ]]; then
  [[ "${STUB_SECRET_EXISTS:-no}" == "yes" ]] && { echo "dmFsdWU="; exit 0; } || exit 1
fi
if [[ "$*" == *"create secret"* ]]; then
  for arg in "$@"; do [[ "$arg" == --from-literal=* ]] && echo "$arg" >> "$STUB_LITERALS"; done
  echo "kind: Secret"
fi
[[ "$*" == *"apply"* ]] && cat > /dev/null
exit 0
"""


@pytest.fixture
def env(tmp_path):
    stub = tmp_path / "kubectl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    environ = dict(os.environ)
    environ.update(
        PATH=f"{tmp_path}:{environ['PATH']}",
        STUB_LOG=str(tmp_path / "calls.log"),
        STUB_LITERALS=str(tmp_path / "literals.log"),
    )
    return environ, tmp_path


def run(args, environ, cwd):
    return subprocess.run(
        [str(SCRIPT), *args], env=environ, cwd=cwd, capture_output=True, text=True
    )


def test_creates_secret_with_three_random_keys_and_prints_no_values(env):
    environ, tmp = env
    result = run([], environ, tmp)
    assert result.returncode == 0, result.stderr
    literals = (tmp / "literals.log").read_text().split()
    pairs = [item.removeprefix("--from-literal=").split("=", 1) for item in literals]
    assert [key for key, _ in pairs] == ["redis-password", "webhook-secret", "api-key"]
    values = [value for _, value in pairs]
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in values)
    assert len(set(values)) == 3
    assert not any(value in result.stdout + result.stderr for value in values)


def test_existing_complete_secret_is_left_alone(env):
    environ, tmp = env
    environ["STUB_SECRET_EXISTS"] = "yes"
    result = run([], environ, tmp)
    assert result.returncode == 0
    assert "create secret" not in (tmp / "calls.log").read_text()


def test_rotate_replaces_existing_secret(env):
    environ, tmp = env
    environ["STUB_SECRET_EXISTS"] = "yes"
    assert run(["--rotate"], environ, tmp).returncode == 0
    assert "create secret" in (tmp / "calls.log").read_text()


def test_dry_run_changes_nothing(env):
    environ, tmp = env
    result = run(["--dry-run"], environ, tmp)
    assert result.returncode == 0
    assert "create secret" not in (tmp / "calls.log").read_text()


def test_rejects_bad_namespace_and_unknown_option(env):
    environ, tmp = env
    assert run(["--namespace", "Bad_NS"], environ, tmp).returncode == 1
    assert run(["--nope"], environ, tmp).returncode == 1


def test_compose_mode_writes_a_private_env_file_once(env):
    environ, tmp = env
    assert run(["--compose", "out.env"], environ, tmp).returncode == 0
    target = tmp / "out.env"
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    original = target.read_text()
    assert {line.split("=")[0] for line in original.split()} == {
        "MANA_REDIS_PASSWORD", "MANA_WEBHOOK_SECRET", "MANA_API_KEY", "GRAFANA_ADMIN_PASSWORD",
    }
    run(["--compose", "out.env"], environ, tmp)
    assert target.read_text() == original
    run(["--compose", "out.env", "--rotate"], environ, tmp)
    assert target.read_text() != original
