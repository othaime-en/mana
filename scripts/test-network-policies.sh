#!/usr/bin/env bash
# Prove that kubernetes/security/network-policies.yaml allows Mana's traffic and
# blocks everything else, using stand-in pods on a cluster whose CNI enforces policy.
set -euo pipefail

CALICO_VERSION="v3.33.0"
CALICO_SHA256="2de8f47595fb9c41b3f47d7b767a1f8e72ecf84057af834738ff12689a234da5"
IMAGE="busybox:1.37"
NAMESPACES=(production staging orchestrator monitoring ci-runners other)

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--install-calico] [--keep] [--help]

Run against a Kind cluster created without its default CNI:
  kind create cluster --name mana-netpol --config kubernetes/security/tests/kind-calico.yaml
  $(basename "$0") --install-calico

--install-calico  Install Calico ${CALICO_VERSION} (manifest verified by SHA-256) first
--keep            Leave the test namespaces in place for inspection

Refuses to run unless the current kubectl context is a kind cluster, because it
creates and deletes the namespaces production, staging and orchestrator.
USAGE
}

install_calico=false
keep=false
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --install-calico) install_calico=true ;;
    --keep) keep=true ;;
    *) usage >&2; echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 1; }
context="$(kubectl config current-context)"
[[ "$context" == kind-* ]] || { echo "Refusing to run against context '$context': not a kind cluster" >&2; exit 1; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"

cleanup() {
  rm -rf "$work"
  if [[ "$keep" == false ]]; then
    kubectl delete namespace "${NAMESPACES[@]}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ "$install_calico" == true ]]; then
  manifest="$work/calico.yaml"
  curl --fail --silent --show-error --location --retry 3 --output "$manifest" \
    "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml"
  echo "${CALICO_SHA256}  ${manifest}" | sha256sum --check --status \
    || { echo "Calico manifest checksum mismatch" >&2; exit 1; }
  kubectl apply -f "$manifest" >/dev/null
  kubectl -n kube-system rollout status daemonset/calico-node --timeout=300s
  kubectl -n kube-system rollout status deployment/calico-kube-controllers --timeout=300s
fi

for ns in "${NAMESPACES[@]}"; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
kubectl apply -f "$root/kubernetes/security/network-policies.yaml" >/dev/null

server() {
  kubectl -n "$1" run "$2" --image="$IMAGE" --labels="app=$3" --restart=Never --command -- \
    sh -c "mkdir -p /www && echo ok > /www/index.html && exec httpd -f -p $4 -h /www" >/dev/null
}
idle() {
  kubectl -n "$1" run "$2" --image="$IMAGE" --labels="app=$3" --restart=Never --command -- \
    sleep 3600 >/dev/null
}

server production sample-app sample-app 5000
server orchestrator orchestrator orchestrator 8000
server orchestrator redis redis 6379
idle orchestrator intruder intruder
idle monitoring client client
idle ci-runners client client
idle other client client

for ns in production orchestrator monitoring ci-runners other; do
  kubectl -n "$ns" wait --for=condition=Ready pod --all --timeout=180s >/dev/null
done

failures=0
check() {
  local want="$1" from_ns="$2" from_pod="$3" to_ns="$4" to_pod="$5" port="$6" got="" ip
  ip="$(kubectl -n "$to_ns" get pod "$to_pod" -o jsonpath='{.status.podIP}')"
  for _ in {1..10}; do
    if kubectl -n "$from_ns" exec "$from_pod" -- wget -q -T 3 -O /dev/null "http://${ip}:${port}" >/dev/null 2>&1; then
      got=allow
    else
      got=deny
    fi
    [[ "$got" == "$want" ]] && break
    sleep 3
  done
  if [[ "$got" == "$want" ]]; then
    echo "ok    ${want} ${from_ns}/${from_pod} -> ${to_ns}/${to_pod}:${port}"
  else
    echo "FAIL  expected ${want}, got ${got}: ${from_ns}/${from_pod} -> ${to_ns}/${to_pod}:${port}"
    failures=$((failures + 1))
  fi
}

check allow monitoring client orchestrator orchestrator 8000
check allow ci-runners client orchestrator orchestrator 8000
check allow orchestrator orchestrator orchestrator redis 6379
check allow orchestrator orchestrator production sample-app 5000
check allow monitoring client production sample-app 5000

check deny other client orchestrator orchestrator 8000
check deny other client production sample-app 5000
check deny ci-runners client production sample-app 5000
check deny orchestrator intruder orchestrator redis 6379
check deny monitoring client orchestrator redis 6379
check deny production sample-app orchestrator orchestrator 8000

if (( failures > 0 )); then
  echo "${failures} check(s) failed. If every deny check failed, the CNI is not enforcing NetworkPolicy." >&2
  exit 1
fi
echo "All network policy checks passed"
