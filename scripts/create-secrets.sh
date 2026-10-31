#!/usr/bin/env bash
# Generate the secrets Mana needs and store them without printing them.
set -euo pipefail

SECRET_NAME="orchestrator-secrets"
KEYS=(redis-password webhook-secret api-key)

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--namespace NS] [--rotate] [--dry-run]
       $(basename "$0") --compose [FILE] [--rotate]
       $(basename "$0") --help

Create the Kubernetes Secret "${SECRET_NAME}" (keys: ${KEYS[*]}) with random
values. An existing complete Secret is left untouched unless --rotate is given.
Values are never printed.

Options:
  --namespace NS   Namespace of the Secret (default: orchestrator)
  --rotate         Replace existing values with new ones
  --dry-run        Show what would happen, change nothing
  --compose [FILE] Write MANA_* values for docker compose to FILE (default .env,
                   mode 0600) instead of touching a cluster

After creating or rotating, copy the webhook secret into GitHub:
  kubectl -n orchestrator get secret ${SECRET_NAME} -o jsonpath='{.data.webhook-secret}' \\
    | base64 -d | gh secret set MANA_WEBHOOK_SECRET
and restart the orchestrator: kubectl -n orchestrator rollout restart deployment/orchestrator
USAGE
}

die() { echo "Error: $*" >&2; exit 1; }

namespace="orchestrator"
rotate=false
dry_run=false
compose=false
env_file=".env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --namespace) [[ $# -ge 2 ]] || die "--namespace needs a value"; namespace="$2"; shift 2 ;;
    --rotate) rotate=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --compose)
      compose=true; shift
      if [[ $# -gt 0 && "$1" != --* ]]; then env_file="$1"; shift; fi
      ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ "$namespace" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || die "invalid namespace: $namespace"
command -v openssl >/dev/null || die "openssl is required"

random_value() { openssl rand -hex 32; }

if $compose; then
  if [[ -e "$env_file" && $rotate == false ]]; then
    echo "$env_file already exists; use --rotate to replace it"
    exit 0
  fi
  if $dry_run; then echo "would write $env_file"; exit 0; fi
  umask 077
  {
    echo "MANA_REDIS_PASSWORD=$(random_value)"
    echo "MANA_WEBHOOK_SECRET=$(random_value)"
    echo "MANA_API_KEY=$(random_value)"
    echo "GRAFANA_ADMIN_PASSWORD=$(random_value)"
  } > "$env_file"
  echo "wrote $env_file"
  exit 0
fi

command -v kubectl >/dev/null || die "kubectl is required"

if kubectl -n "$namespace" get secret "$SECRET_NAME" >/dev/null 2>&1; then
  if [[ $rotate == false ]]; then
    for key in "${KEYS[@]}"; do
      kubectl -n "$namespace" get secret "$SECRET_NAME" \
        -o go-template="{{index .data \"${key}\"}}" 2>/dev/null | grep -q . \
        || die "secret $SECRET_NAME exists but lacks key $key; rerun with --rotate"
    done
    echo "secret $SECRET_NAME already complete in $namespace; use --rotate to replace it"
    exit 0
  fi
fi

if $dry_run; then
  echo "would create or replace secret $SECRET_NAME in $namespace with keys: ${KEYS[*]}"
  exit 0
fi

kubectl get namespace "$namespace" >/dev/null 2>&1 || kubectl create namespace "$namespace" >/dev/null

args=()
for key in "${KEYS[@]}"; do
  args+=("--from-literal=${key}=$(random_value)")
done
kubectl -n "$namespace" create secret generic "$SECRET_NAME" "${args[@]}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "secret $SECRET_NAME written to namespace $namespace"
