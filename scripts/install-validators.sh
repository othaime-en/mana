#!/usr/bin/env bash
set -euo pipefail

PROMETHEUS_VERSION="3.15.1"
PROMETHEUS_SHA256="6a726885328ad9efebbf36e876ce2fb6225468a71fd1e7ce3ba666d5f4d97a84"
ALERTMANAGER_VERSION="0.34.1"
ALERTMANAGER_SHA256="265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8"
ACTIONLINT_VERSION="1.7.12"
ACTIONLINT_SHA256="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--help] [INSTALL_DIR]

Install promtool ${PROMETHEUS_VERSION}, amtool ${ALERTMANAGER_VERSION} and
actionlint ${ACTIONLINT_VERSION} (linux amd64) into INSTALL_DIR, verifying each
download against a pinned SHA-256. Safe to run again.

INSTALL_DIR defaults to ./.tools/bin. Put it on PATH, then run:
  promtool check rules monitoring/prometheus/alerts.yml monitoring/prometheus/recording-rules.yml
  promtool check config --syntax-only monitoring/prometheus/prometheus.yml
  amtool check-config monitoring/alertmanager/alertmanager.yml
  actionlint
USAGE
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
esac

if [[ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]]; then
  echo "Only linux amd64 is supported; install the tools from their release pages." >&2
  exit 1
fi

for tool in curl tar sha256sum; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done

install_dir="${1:-.tools/bin}"
mkdir -p "$install_dir"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

fetch() {
  local url="$1" sha256="$2" archive="$work_dir/archive.tgz"
  curl --fail --silent --show-error --location --retry 3 --max-time 180 --output "$archive" "$url"
  echo "${sha256}  ${archive}" | sha256sum --check --status \
    || { echo "Checksum mismatch for ${url}" >&2; exit 1; }
}

install_tool() {
  local name="$1" url="$2" sha256="$3" member="$4"
  if [[ -x "$install_dir/$name" ]]; then
    echo "$name already installed"
    return
  fi
  fetch "$url" "$sha256"
  tar --extract --gzip --file "$work_dir/archive.tgz" --directory "$install_dir" \
    --strip-components="$5" "$member"
  echo "installed $name"
}

release="https://github.com"
install_tool promtool \
  "$release/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz" \
  "$PROMETHEUS_SHA256" "prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool" 1
install_tool amtool \
  "$release/prometheus/alertmanager/releases/download/v${ALERTMANAGER_VERSION}/alertmanager-${ALERTMANAGER_VERSION}.linux-amd64.tar.gz" \
  "$ALERTMANAGER_SHA256" "alertmanager-${ALERTMANAGER_VERSION}.linux-amd64/amtool" 1
install_tool actionlint \
  "$release/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" \
  "$ACTIONLINT_SHA256" "actionlint" 0
