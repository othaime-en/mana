# Release Notes

This page records notable changes to MANA, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

!!! info "Versioning status"
MANA has not yet published tagged releases. The orchestrator API reports version `1.0.0` and the sample app defaults to `1.0.0`, but the repository has no Git tags. The entries below are reconstructed from the commit history and pull requests (#1 to #9) and grouped by milestone. When you cut your first release, tag it (`git tag v1.0.0`) and move the relevant items under that heading.

## Unreleased

### Added

- **Authentication (SEC-01).** `POST /webhook/deployment` requires an HMAC-SHA256 signature over `<timestamp>.<body>` (`X-Mana-Timestamp`, `X-Mana-Signature`, 300 s window); the operator endpoints require `X-API-Key`. `WEBHOOK_SECRET` and `API_KEY` take comma-separated lists for rotation. Rejected requests return a generic `401`, are counted as `webhook_errors_total{reason="unauthorized"}` and raise the new `WebhookAuthenticationFailures` alert.
- `REDIS_PASSWORD`, `REDIS_TLS` and `REDIS_TLS_CA_FILE` (SEC-03).
- `scripts/create-secrets.sh` creates `orchestrator-secrets` with random values; `scripts/notify-orchestrator.sh` builds, signs and sends webhooks and is used by both workflows (SEC-05, SEC-01).
- Default-deny NetworkPolicies with explicit allow rules, `scripts/test-network-policies.sh` and the `network-policy-test.yml` workflow (Calico on Kind) (SEC-02, SEC-06). Not run on a cluster.
- Namespaced RBAC for the orchestrator (`orchestrator/k8s/rbac.yaml`), a `ci-deployer` identity for the deploy workflow, Pod Security `restricted` namespace labels (`kubernetes/security/namespaces.yaml`) (SEC-04, SEC-07, SEC-09).
- `.github/dependabot.yml`, blocking `pip-audit`, Trivy and Bandit jobs (SEC-09, SEC-10).
- Orchestrator metrics that dashboards and alerts depend on: `deployment_duration_seconds` (now with a `status` label), `active_deployments` (read from Redis at scrape time), `rollback_decision_duration_seconds`, `health_checks_total`, `webhook_requests_total`, `webhook_errors_total` and `redis_connection_errors_total`, plus the new `mana_recovery_duration_seconds` (first failure to a successful retry or verified rollback). Dashboards gain panels for recovery time, health check results and webhook traffic (OBS-03, OBS-09).
- Deployments are persisted from the `in_progress` webhook with `started_at`, `first_failure_at` and `finished_at`, which `GET /deployment/{id}` returns (CORE-12).
- `tests/test_monitoring_contract.py` fails when an alert, recording rule or dashboard panel uses a metric nothing provides, or a dashboard uses a datasource placeholder it does not declare.
- Workflow `validate-monitoring.yml` runs `promtool`, `amtool`, `alloy validate` and `actionlint`; `scripts/install-validators.sh` installs the same pinned, checksum-verified tools locally (CI-05, partly).
- Grafana Alloy configuration in `monitoring/alloy/` for Kubernetes and for Docker Compose.

### Changed

- **Breaking: the orchestrator refuses to start without `WEBHOOK_SECRET` and `API_KEY`** (at least 32 characters) unless `AUTH_DISABLED=true`. Unsigned webhooks and keyless operator calls get `401`. The workflows need a `MANA_WEBHOOK_SECRET` repository secret equal to the orchestrator's `webhook-secret`.
- **Breaking: the orchestrator's ClusterRole is gone.** A Role and RoleBinding in `production`, `staging` and `canary` grant `get`/`patch` on Deployments and `list` on ReplicaSets and Pods; `/health` reads the API server version instead of listing namespaces. Create the three namespaces before applying `orchestrator/k8s/rbac.yaml`.
- **Breaking: Redis requires a password** (`redis-password` in `orchestrator-secrets`); Terraform `redis_password` is removed and every Terraform password is generated. `admin123` is gone from Terraform, Helm values, Compose and the Makefile.
- `kubernetes/security/network-policies.yaml` now holds NetworkPolicies only (default deny, selectors on `kubernetes.io/metadata.name`); the PodSecurityPolicy, OpenShift SCC, duplicate RBAC and placeholder Secret are removed and quotas moved to `resource-quotas.yaml`.
- Workloads run with `seccomp` `RuntimeDefault`, a read-only root filesystem with an `emptyDir` at `/tmp`, and no service-account token (sample app, Redis); Redis gains probes. `production`, `staging`, `canary` and `orchestrator` enforce Pod Security `restricted`.
- All GitHub Actions are pinned by commit SHA (same majors as before); images are pushed with `GITHUB_TOKEN` instead of `GHCR_PAT`; `KUBE_CONFIG` and inputs are passed through `env:`; failed notifications raise a `::warning::` annotation.
- Dependencies: orchestrator `fastapi` 0.143.2 with `starlette` 1.8.0; sample app `Flask` 3.1.3 and `gunicorn` 23.0.0 (advisories found by `pip-audit` on the resolved trees). The sample app's test tools moved to `sample-app/requirements-dev.txt`.
- **`deployments_total` now counts final outcomes** (`success`, `failed`, `rolled_back`), once per transition, instead of every webhook. `in_progress` events and retried attempts are no longer counted, so success-rate rules are correct (OBS-02). Queries on `status="in_progress"` stop returning data.
- `GET /metrics/deployment/{namespace}/{deployment_name}` moves to `GET /deployments/{namespace}/{deployment_name}/status`; the old path is gone. `GET /metrics` is served directly instead of redirecting (OBS-04, OBS-05).
- Every log line is one JSON object, with audit fields merged in, and `LOG_LEVEL` and `ENABLE_AUDIT_LOGGING` are applied. An invalid `LOG_LEVEL` stops startup (OBS-01, OBS-08).
- Because `in_progress` now stores webhook metadata, a failure webhook without `workflow_run_id` is retried when the deployment's `in_progress` event carried one, instead of alerting.
- Promtail is replaced by Grafana Alloy in Compose and Terraform; Terraform builds one `PrometheusRule` and the dashboard ConfigMaps from the files in `monitoring/` instead of its own copies (OBS-07, OBS-10). Not validated or applied: no Terraform binary or cluster was available.
- `actions/cache` and `codecov/codecov-action` are bumped to v6.1.0 and v7.1.1 (CI-04, partly).

- Complete documentation set under `docs/` (published with MkDocs): architecture, configuration, usage, reference, contributing and about sections (PR #9).

### Fixed

- `DeploymentFailureRate` and `HighWebhookFailureRate` divided series with different label sets and could never evaluate correctly; two quantile alerts lacked `sum by (le)`; `DeploymentReplicasMismatch` described a label that does not exist; the Compose Prometheus never loaded the recording rules; the standalone scrape jobs built invalid target addresses; `alertmanager.yml` did not load (invalid email `subject` field); provisioned dashboards could not resolve their datasource placeholders (OBS-02, OBS-07, OBS-11).
- The first failure webhook for a deployment now returns `retry` instead of `alert`: the webhook's `metadata`, including `workflow_run_id`, is carried into the failure state (PR #31).
- `deployment_id` is now `deploy-<run_id>-<namespace>` and the image version is `v<sha7>`, so a workflow re-run reports the same deployment and the retry counter accumulates (CORE-01). Webhook metadata now carries `run_attempt`.
- `failure_type: smoke_test_failure` is accepted. `status` and `failure_type` are validated as enums, so unknown values return HTTP 422 instead of 500, an empty `failure_type` counts as absent, and `status: failed` requires a `failure_type` (CORE-13, TEST-04).
- Rollback guards (CORE-16): the orchestrator never rolls back for `build_failure` or `test_failure`, and rolls back only when the image tag running in the cluster equals the failed version. Otherwise it answers `skip` (another version is live) or `alert` (the live version cannot be read or compared) and writes a `rollback_skipped` audit event for a skip. The workflows no longer run `kubectl rollout undo`, so the orchestrator is the only rollback owner (CORE-04).
- `ROLLBACK_THRESHOLD` is removed (CORE-03). It had no effect unless larger than `MAX_RETRIES`. Rollback now starts when `MAX_RETRIES` retries are exhausted. A leftover `ROLLBACK_THRESHOLD` is ignored with a deprecation warning, and the `rollback_threshold` field is gone from `GET /config` and from the Terraform `orchestrator` module (remove it from your `.tfvars` if you set it).
- `rollback_deployment` is hardened (CORE-09). It sends a minimal patch (image and version labels) instead of the whole Deployment, builds the target image correctly for registry ports and digests, fails loudly when it cannot identify the container instead of silently changing nothing, verifies the image after the patch, and waits for the patched generation to finish rolling out so old ready replicas cannot pass the health check. All errors are caught and audited, and `rollback_initiated` records the real running version and the reason (manual rollbacks no longer say "Exceeded retry threshold").
- CI runs the orchestrator tests with a 75% coverage gate, and the sample-app tests with an 80% gate (CI-01, CI-02). Test tools moved out of the orchestrator runtime image into `orchestrator/requirements-dev.txt`, which also adds the missing `httpx`. The sample-app lint step (flake8 and black, pinned) is now blocking, and `sample-app/src` was formatted to pass it.
- Timestamps in audit events and sample-app logs use timezone-aware UTC with the same `...Z` format. Sample-app shutdown cleanup runs once instead of twice on SIGTERM (TEST-03).
- Both components can now share one virtualenv (`pip install -r requirements-dev.txt`). The sample app no longer lists `requests`, which it never imported; the orchestrator's `requests` moves from 2.32.3 to 2.34.2, past the fixes in 2.32.4 (CVE-2024-47081) and 2.33.0; the sample app's `pytest` and `pytest-cov` match the orchestrator's (8.3.3 and 5.0.0). A test fails if shared pins diverge.

### Known issues

These are confirmed by the current code and are tracked in [Data Flow: Known Behaviours](../architecture/data-flow.md#known-behaviours-and-limitations):

- Build and test failures reported with `namespace: ci` are counted as `failed` in `deployments_total{namespace="ci"}` and lower the aggregate success rate (CORE-14).
- A counter's first increment is invisible to `rate()` (the series appears already at 1), so the first deployment of each new namespace and status is missed by rate-based panels.
- A signed webhook can be replayed for 300 seconds, and two replicas can both act on one event (CORE-06). `/health`, `/metrics` and the OpenAPI routes are unauthenticated by design.
- The NetworkPolicies, Pod Security admission, the read-only root filesystem under gunicorn, the Trivy gate, the Terraform changes and the first runs of the new workflows have not been exercised on a cluster or on GitHub.
- Several Makefile targets reference scripts that are not in the repository.

## Milestone 6: CI/CD pipeline and hardening (February 2026)

### Added

- Reusable GitHub Actions workflows: `build-and-test.yml`, `local-deployment-test.yml` (Kind), `deploy-environment.yml`, and the `main-pipeline.yml` entry point with manual staging/production deployment (PR #7).
- Webhook notifications from the pipeline, including `workflow_run_id` for retries.
- `security-scan.yml`: Snyk, Trivy and Bandit scans.
- Orchestrator Kubernetes manifests (`orchestrator/k8s/`) and Redis manifest; orchestrator image built in CI (PR #6).

### Changed

- Gunicorn worker timeout increased; probe delays and image pull policy adjusted; `readOnlyRootFilesystem` set to `false` for the sample app (PR #8).
- Version tags always start with a letter (`v<sha>-<timestamp>`).

## Milestone 5: Infrastructure as Code (February 2026)

### Added

- Terraform root module and modules for namespaces, sample app, orchestrator (including optional Redis), security (NetworkPolicies, quotas, limit ranges) and monitoring (kube-prometheus-stack, Loki, ServiceMonitors, PrometheusRules, Grafana dashboard) (PR #5).
- Staging copy of the sample app, `image_registry` and `failure_rate` variables, shared labels, and `validate-terraform.sh`.
- `scripts/deploy.sh`, a `Makefile`, `docker-compose.yml` for a local stack, and Kubernetes security manifests.

## Milestone 4: Observability (February 2026)

### Added

- Prometheus configuration, alert rules (deployment, orchestrator, application, infrastructure and monitoring groups) and recording rules (PR #4).
- Loki and Promtail configuration with 30-day retention.
- Alertmanager routing with Slack, PagerDuty and email receivers and notification templates.
- Grafana provisioning and four dashboards: Self-Healing Overview, Deployment Metrics, Application Metrics, Infrastructure.

## Milestone 3: Orchestrator maturity (February 2026)

### Added

- Centralised, validated configuration (`config.py`) and `.env.example` (PR #3).
- Application-level health checks against pod `/health` endpoints, in addition to replica readiness.
- Exponential backoff for retries (`INITIAL_BACKOFF`, `MAX_BACKOFF`, `BACKOFF_MULTIPLIER`).
- GitHub Actions workflow re-run integration for retries.
- Structured audit logging (`AuditLogger`) covering the full recovery lifecycle (PR #2).
- Endpoints for recent deployments and configuration; last-successful-version tracking in Redis.

### Changed

- Orchestrator initialisation uses configuration by default (`use_config=True`); tests use `use_config=False`.

## Milestone 2: Sample application hardening (February 2026)

### Added

- Environment-driven, validated configuration for the sample app.
- Structured JSON logging with request correlation IDs.
- Graceful shutdown handling for SIGTERM and SIGINT (PR #1).
- Dockerfiles for the sample app (Gunicorn, non-root) and orchestrator.

### Fixed

- The first failure webhook for a deployment now returns `retry` instead of `alert`: the webhook's `metadata`, including `workflow_run_id`, is carried into the failure state (PR #31).
- `app.start_time` initialised at import so readiness works under WSGI servers.

## Milestone 1: Initial implementation (December 2025 to January 2026)

### Added

- Repository bootstrap, MIT licence and `.gitignore` (December 2025).
- Flask sample app with `/health`, `/ready` (startup delay), sample data and stress endpoints, Prometheus metrics, random failure simulation and error handlers.
- Kubernetes manifests: Deployment (rolling updates, probes, security context, resource limits), Service, Ingress, PodDisruptionBudget and HorizontalPodAutoscaler.
- First version of the orchestrator: state model and enums, Redis state persistence, deployment health check, previous-version lookup, rollback and failure handling.
- FastAPI endpoints: webhook, health check, deployment state and deployment metrics.
- Initial unit tests for the app and orchestrator.

## Upgrade Notes

No releases have been published, but moving from the previous commit needs these steps in order:

1. Create the namespaces (`kubectl apply -f kubernetes/security/namespaces.yaml`; `orchestrator` is created by its manifest) and run `scripts/create-secrets.sh`.
2. Apply `orchestrator/k8s/rbac.yaml`, then delete the old `orchestrator-role` ClusterRole and ClusterRoleBinding.
3. Apply `redis.yaml` and `deployment.yaml`, and restart Redis and the orchestrator together so both use the new password.
4. Copy the webhook secret into the `MANA_WEBHOOK_SECRET` repository secret.
5. Apply `kubernetes/security/network-policies.yaml` last, after running `scripts/test-network-policies.sh` on a cluster that enforces policy.
6. With Terraform, expect the secrets, NetworkPolicies, Roles and the Grafana password to be replaced; the state then holds generated secrets.

As a general rule:

- Redis state expires after 24 hours, so restarting the orchestrator or upgrading it does not require a migration.
- `last_successful:<namespace>:<app>` keys do not expire; keep them if you want rollbacks to work immediately after an upgrade.
- Configuration is read at startup. Restart the orchestrator after changing environment variables.
