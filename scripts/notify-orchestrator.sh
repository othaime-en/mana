#!/usr/bin/env bash
# Send a signed deployment status webhook to the Mana orchestrator.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: notify-orchestrator.sh [--help]

Reads its input from the environment so no value is interpolated into a shell
script by the workflow engine:

  ORCHESTRATOR_URL       Base URL of the orchestrator; when empty nothing is sent
  MANA_WEBHOOK_SECRET    Shared secret used to sign the request (required)
  MANA_DEPLOYMENT_ID     Stable deployment identity (required)
  MANA_NAMESPACE         Target namespace (required)
  MANA_VERSION           Version being deployed (required)
  MANA_STATUS            in_progress | success | failed (required)
  MANA_FAILURE_TYPE      Required when MANA_STATUS=failed:
                         build_failure | test_failure | deployment_failure |
                         health_check_failure | smoke_test_failure | timeout
  MANA_APP_NAME          Application name (default: sample-app)
  MANA_WORKFLOW_RUN_ID   Run to re-run on failure; omitted from the payload when unset

GITHUB_SHA, GITHUB_ACTOR, GITHUB_REF_NAME and GITHUB_RUN_ATTEMPT are added to
the metadata when present. The body is signed as HMAC-SHA256 over
"<unix timestamp>.<body>" and sent with X-Mana-Timestamp and X-Mana-Signature.
Exit status is 1 when the orchestrator cannot be notified.
USAGE
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

fail() {
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::warning::Mana webhook not delivered: $*"
  fi
  echo "Error: $*" >&2
  exit 1
}

if [[ -z "${ORCHESTRATOR_URL:-}" ]]; then
  echo "ORCHESTRATOR_URL not set; skipping notification"
  exit 0
fi

for tool in curl jq python3; do
  command -v "$tool" >/dev/null || fail "$tool is required"
done

for name in MANA_WEBHOOK_SECRET MANA_DEPLOYMENT_ID MANA_NAMESPACE MANA_VERSION MANA_STATUS; do
  [[ -n "${!name:-}" ]] || fail "$name is required"
done

case "$ORCHESTRATOR_URL" in
  http://*|https://*) ;;
  *) fail "ORCHESTRATOR_URL must start with http:// or https://" ;;
esac

case "$MANA_STATUS" in
  in_progress|success) failure_type="" ;;
  failed)
    failure_type="${MANA_FAILURE_TYPE:-}"
    [[ -n "$failure_type" ]] || fail "MANA_FAILURE_TYPE is required when MANA_STATUS=failed"
    ;;
  *) fail "unsupported MANA_STATUS: $MANA_STATUS" ;;
esac

body="$(jq -cn \
  --arg id "$MANA_DEPLOYMENT_ID" \
  --arg ns "$MANA_NAMESPACE" \
  --arg app "${MANA_APP_NAME:-sample-app}" \
  --arg version "$MANA_VERSION" \
  --arg status "$MANA_STATUS" \
  --arg failure "$failure_type" \
  --arg commit "${GITHUB_SHA:-}" \
  --arg author "${GITHUB_ACTOR:-}" \
  --arg branch "${GITHUB_REF_NAME:-}" \
  --arg attempt "${GITHUB_RUN_ATTEMPT:-}" \
  --arg run "${MANA_WORKFLOW_RUN_ID:-}" \
  '{
    deployment_id: $id, namespace: $ns, app_name: $app, version: $version,
    status: $status,
    failure_type: (if $failure == "" then null else $failure end),
    metadata: ({commit: $commit, author: $author, branch: $branch, run_attempt: $attempt}
               + (if $run == "" then {} else {workflow_run_id: $run} end)
               | with_entries(select(.value != "")))
  }')"

timestamp="$(date +%s)"
signature="$(printf '%s.%s' "$timestamp" "$body" | python3 -c '
import hashlib, hmac, os, sys
key = os.environ["MANA_WEBHOOK_SECRET"].encode()
print("sha256=" + hmac.new(key, sys.stdin.buffer.read(), hashlib.sha256).hexdigest())
')"

curl --fail-with-body --silent --show-error \
  --connect-timeout 10 --max-time 360 --retry 2 --retry-connrefused \
  --request POST "${ORCHESTRATOR_URL%/}/webhook/deployment" \
  --header "Content-Type: application/json" \
  --header "X-Mana-Timestamp: ${timestamp}" \
  --header "X-Mana-Signature: ${signature}" \
  --data-binary "$body" \
  || fail "orchestrator did not accept the ${MANA_STATUS} event for ${MANA_DEPLOYMENT_ID}"
echo
