#!/usr/bin/env bash
# Deploy a deliberately broken version of the sample app and time how long Mana
# takes to put the previous version back (first failure to a verified rollback).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY_SCRIPT="${NOTIFY_SCRIPT:-$SCRIPT_DIR/../notify-orchestrator.sh}"

usage() {
  cat <<'USAGE'
Usage: simulate-failure.sh <mode> --yes [options]

Modes (each needs the sample app image to include fault injection):
  crash      FAIL_ON_START: the container crashes on start (CrashLoopBackOff)
  unhealthy  FAIL_HEALTH_AFTER=5: /health answers 500 five seconds after start
  unready    READY_NEVER: /ready never answers 200, so the rollout never completes
  oom        LEAK_MB_PER_S=50: memory grows until the container is OOMKilled

Steps: record GOOD_VERSION as successful, tag the same image as a bad version
with the fault switched on, patch the Deployment, report the failure to Mana, wait
for status rolled_back and print the recovery time and the live image.

Options:
  --yes                Required: confirms the Deployment in the current context may be patched
  --good-version V     Version that is running and becomes the rollback target (default: 1.0.0)
  --namespace NS       Namespace of the Deployment (default: production)
  --app NAME           Deployment and container name (default: sample-app)
  --image-repo REPO    Image repository without a tag (default: sample-app)
  --failure-type T     failure_type reported to Mana (default: health_check_failure)
  --kind-cluster NAME  Retag the image locally with docker and load it into this kind cluster
  --timeout SECONDS    How long to wait for the rollback (default: 180)
  --any-context        Do not insist on a kind-* kubectl context
  -h, --help           Show this help

Environment:
  ORCHESTRATOR_URL     Base URL of the orchestrator, for example http://localhost:8000 (required)
  MANA_WEBHOOK_SECRET  Webhook signing secret (required)
  MANA_API_KEY         Operator API key used to read the deployment state (required)

Exit status: 0 when the version was rolled back, 1 when it was not or on error, 2 on bad usage.
USAGE
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

usage_error() {
  usage >&2
  echo "Error: $*" >&2
  exit 2
}

mode=""
confirmed=false
good_version="1.0.0"
namespace="production"
app="sample-app"
image_repo="sample-app"
failure_type="health_check_failure"
kind_cluster=""
wait_seconds=180
any_context=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --yes) confirmed=true ;;
    --any-context) any_context=true ;;
    --good-version|--namespace|--app|--image-repo|--failure-type|--kind-cluster|--timeout)
      [[ $# -ge 2 ]] || usage_error "$1 needs a value"
      case "$1" in
        --good-version) good_version="$2" ;;
        --namespace) namespace="$2" ;;
        --app) app="$2" ;;
        --image-repo) image_repo="$2" ;;
        --failure-type) failure_type="$2" ;;
        --kind-cluster) kind_cluster="$2" ;;
        --timeout) wait_seconds="$2" ;;
      esac
      shift
      ;;
    -*) usage_error "unknown option: $1" ;;
    *)
      [[ -z "$mode" ]] || usage_error "only one mode may be given"
      mode="$1"
      ;;
  esac
  shift
done

[[ -n "$mode" ]] || usage_error "a mode is required"
case "$mode" in
  crash) fault_env='[{"name":"FAIL_ON_START","value":"true"}]' ;;
  unhealthy) fault_env='[{"name":"FAIL_HEALTH_AFTER","value":"5"}]' ;;
  unready) fault_env='[{"name":"READY_NEVER","value":"true"}]' ;;
  oom) fault_env='[{"name":"LEAK_MB_PER_S","value":"50"}]' ;;
  *) usage_error "unknown mode: $mode" ;;
esac
$confirmed || usage_error "--yes is required: this patches the Deployment $app in namespace $namespace"
[[ "$wait_seconds" =~ ^[0-9]+$ ]] || usage_error "--timeout must be a whole number of seconds"
case "$failure_type" in
  build_failure|test_failure|deployment_failure|health_check_failure|smoke_test_failure|timeout) ;;
  *) usage_error "unsupported failure type: $failure_type" ;;
esac

for name in ORCHESTRATOR_URL MANA_WEBHOOK_SECRET MANA_API_KEY; do
  [[ -n "${!name:-}" ]] || fail "$name is required"
done
for tool in kubectl curl jq; do
  command -v "$tool" >/dev/null || fail "$tool is required"
done
[[ -z "$kind_cluster" ]] || command -v docker >/dev/null || fail "docker is required with --kind-cluster"
[[ -x "$NOTIFY_SCRIPT" ]] || fail "notify script not found: $NOTIFY_SCRIPT"

context="$(kubectl config current-context)"
if ! $any_context && [[ "$context" != kind-* ]]; then
  fail "refusing to run against context '$context': not a kind cluster (use --any-context to override)"
fi
echo "Context: $context, deployment: $namespace/$app, good version: $good_version"

bad_version="${good_version}-${mode}-$(date +%s)"
deployment_id="demo-${mode}-$(date +%s)"

notify() {
  MANA_DEPLOYMENT_ID="$1" MANA_NAMESPACE="$namespace" MANA_APP_NAME="$app" \
    MANA_VERSION="$2" MANA_STATUS="$3" MANA_FAILURE_TYPE="${4:-}" "$NOTIFY_SCRIPT" >/dev/null
}

echo "Recording $good_version as the last successful version"
notify "demo-good-$(date +%s)" "$good_version" success

if [[ -n "$kind_cluster" ]]; then
  echo "Tagging $image_repo:$bad_version and loading it into kind cluster $kind_cluster"
  docker tag "$image_repo:$good_version" "$image_repo:$bad_version"
  kind load docker-image "$image_repo:$bad_version" --name "$kind_cluster"
fi

patch="$(jq -cn --arg app "$app" --arg image "$image_repo:$bad_version" --argjson env "$fault_env" \
  '{spec:{template:{spec:{containers:[{name:$app, image:$image,
    env:([{name:"ENABLE_FAULT_INJECTION",value:"true"}] + $env)}]}}}}')"

echo "Deploying $bad_version with mode $mode"
notify "$deployment_id" "$bad_version" in_progress
kubectl -n "$namespace" patch deployment "$app" --type strategic --patch "$patch"
kubectl -n "$namespace" rollout status "deployment/$app" --timeout=30s || true

echo "Reporting $failure_type to Mana"
started="$(date +%s)"
notify "$deployment_id" "$bad_version" failed "$failure_type"

state_url="${ORCHESTRATOR_URL%/}/deployment/$deployment_id"
status=""
while :; do
  status="$(curl -fsS -H "X-API-Key: $MANA_API_KEY" "$state_url" | jq -r '.status' || true)"
  case "$status" in rolled_back|failed|success) break ;; esac
  (( $(date +%s) - started < wait_seconds )) || fail "no outcome after ${wait_seconds}s (last status: ${status:-unknown})"
  sleep 1
done
elapsed=$(( $(date +%s) - started ))

state="$(curl -fsS -H "X-API-Key: $MANA_API_KEY" "$state_url")"
recovery="$(jq -r 'if .finished_at and .first_failure_at then (.finished_at - .first_failure_at | . * 100 | round / 100 | tostring) else "unknown" end' <<<"$state")"
live="$(kubectl -n "$namespace" get deployment "$app" -o jsonpath='{.spec.template.spec.containers[0].image}')"

echo
echo "Outcome:        $status"
echo "Diagnosis:      $(jq -r '.diagnosis // "none"' <<<"$state")"
echo "Recovery time:  ${recovery} s (first failure to settled, as mana_recovery_duration_seconds)"
echo "Wall clock:     ${elapsed} s since the failure was reported"
echo "Live image:     $live"
curl -fsS "${ORCHESTRATOR_URL%/}/metrics" | grep -E '^mana_recovery_duration_seconds_(sum|count)' || true

[[ "$status" == rolled_back ]] || fail "version $bad_version was not rolled back (status: $status)"
