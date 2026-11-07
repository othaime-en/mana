#!/usr/bin/env bash
# Deploy a deliberately broken version of the sample app and time how long Mana
# takes to put the previous version back (first failure to a verified rollback).
# Modes that break the pod are reported as failures; modes that only make the app
# slow or error-prone pass the pipeline's checks and are caught by Prometheus
# verification, which needs the policy's verification section enabled.
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
  errors     FAILURE_RATE=0.5: half the requests fail with 5xx, but /health stays green
  slow       LATENCY_MS=1500: every request takes 1.5 s more, but /health stays green

crash, unhealthy, unready and oom are reported to Mana as failures. errors and slow
pass the pipeline's own checks, so the script reports success and Mana must catch
them by verifying the deployment against Prometheus (policy verification enabled,
PROMETHEUS_URL set); they need --load-url because the checks need traffic.

Steps: record GOOD_VERSION as successful, tag the same image as a bad version
with the fault switched on, patch the Deployment, report the failure (or, for errors
and slow, the success) to Mana, wait for status rolled_back and print the recovery
time and the live image.

Options:
  --yes                Required: confirms the Deployment in the current context may be patched
  --good-version V     Version that is running and becomes the rollback target (default: 1.0.0)
  --namespace NS       Namespace of the Deployment (default: production)
  --app NAME           Deployment and container name (default: sample-app)
  --image-repo REPO    Image repository without a tag (default: sample-app)
  --failure-type T     failure_type reported to Mana (default: health_check_failure; ignored by errors and slow)
  --load-url URL       Send requests to this URL while waiting (required by errors and slow), for
                       example a port-forward of the Service: http://localhost:8080/api/data
  --kind-cluster NAME  Retag the image locally with docker and load it into this kind cluster
  --timeout SECONDS    How long to wait for the rollback (default: 180; 300 for errors and slow)
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
verified=false
confirmed=false
good_version="1.0.0"
namespace="production"
app="sample-app"
image_repo="sample-app"
failure_type="health_check_failure"
kind_cluster=""
wait_seconds=180
timeout_given=false
load_url=""
any_context=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --yes) confirmed=true ;;
    --any-context) any_context=true ;;
    --good-version|--namespace|--app|--image-repo|--failure-type|--kind-cluster|--timeout|--load-url)
      [[ $# -ge 2 ]] || usage_error "$1 needs a value"
      case "$1" in
        --good-version) good_version="$2" ;;
        --namespace) namespace="$2" ;;
        --app) app="$2" ;;
        --image-repo) image_repo="$2" ;;
        --failure-type) failure_type="$2" ;;
        --kind-cluster) kind_cluster="$2" ;;
        --timeout) wait_seconds="$2"; timeout_given=true ;;
        --load-url) load_url="$2" ;;
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
  errors) fault_env='[{"name":"FAILURE_RATE","value":"0.5"}]'; verified=true ;;
  slow) fault_env='[{"name":"LATENCY_MS","value":"1500"}]'; verified=true ;;
  *) usage_error "unknown mode: $mode" ;;
esac
if $verified; then
  [[ -n "$load_url" ]] || usage_error "mode $mode needs --load-url: the verification checks need traffic"
  [[ "$load_url" =~ ^https?:// ]] || usage_error "--load-url must be an http or https URL"
  $timeout_given || wait_seconds=300
fi
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

load_pid=""
stop_load() {
  if [[ -n "$load_pid" ]]; then
    kill "$load_pid" 2>/dev/null || true
    wait "$load_pid" 2>/dev/null || true
    load_pid=""
  fi
}
trap stop_load EXIT

echo "Deploying $bad_version with mode $mode"
notify "$deployment_id" "$bad_version" in_progress
kubectl -n "$namespace" patch deployment "$app" --type strategic --patch "$patch"

if $verified; then
  kubectl -n "$namespace" rollout status "deployment/$app" --timeout=120s \
    || fail "the rollout of $bad_version did not complete; mode $mode is meant to leave the pods healthy"
  echo "Sending requests to $load_url while Mana verifies the deployment"
  ( while :; do curl -sS -o /dev/null --max-time 5 "$load_url" || true; sleep 0.1; done ) >/dev/null 2>&1 &
  load_pid=$!
  echo "Reporting success to Mana (the pipeline's own checks pass)"
  started="$(date +%s)"
  notify "$deployment_id" "$bad_version" success
else
  kubectl -n "$namespace" rollout status "deployment/$app" --timeout=30s || true
  echo "Reporting $failure_type to Mana"
  started="$(date +%s)"
  notify "$deployment_id" "$bad_version" failed "$failure_type"
fi

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
if $verified; then
  detected="$(jq -r 'if .first_failure_at and .verification.started_at then (.first_failure_at - .verification.started_at | . * 10 | round / 10 | tostring) else "unknown" end' <<<"$state")"
  echo "Detected after: ${detected} s in verification (warmup plus the breaches in a row)"
  echo "Checks:         $(jq -c '.verification.last // {}' <<<"$state")"
fi
reported=failure
$verified && reported=success
echo "Wall clock:     ${elapsed} s since the ${reported} was reported"
echo "Live image:     $live"
curl -fsS "${ORCHESTRATOR_URL%/}/metrics" | grep -E '^mana_recovery_duration_seconds_(sum|count)' || true

[[ "$status" == rolled_back ]] || fail "version $bad_version was not rolled back (status: $status)"
