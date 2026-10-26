# Release Notes

This page records notable changes to MANA, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

!!! info "Versioning status"
MANA has not yet published tagged releases. The orchestrator API reports version `1.0.0` and the sample app defaults to `1.0.0`, but the repository has no Git tags. The entries below are reconstructed from the commit history and pull requests (#1 to #9) and grouped by milestone. When you cut your first release, tag it (`git tag v1.0.0`) and move the relevant items under that heading.

## Unreleased

### Added

- Complete documentation set under `docs/` (published with MkDocs): architecture, configuration, usage, reference, contributing and about sections (PR #9).

### Fixed

- `deployment_id` is now `deploy-<run_id>-<namespace>` and the image version is `v<sha7>`, so a workflow re-run reports the same deployment and the retry counter accumulates (CORE-01). Webhook metadata now carries `run_attempt`.
- `failure_type: smoke_test_failure` is accepted. `status` and `failure_type` are validated as enums, so unknown values return HTTP 422 instead of 500, an empty `failure_type` counts as absent, and `status: failed` requires a `failure_type` (CORE-13, TEST-04).
- Rollback guards (CORE-16): the orchestrator never rolls back for `build_failure` or `test_failure`, and rolls back only when the image tag running in the cluster equals the failed version. Otherwise it answers `skip` (another version is live) or `alert` (the live version cannot be read or compared) and writes a `rollback_skipped` audit event for a skip. The workflows no longer run `kubectl rollout undo`, so the orchestrator is the only rollback owner (CORE-04).
- `ROLLBACK_THRESHOLD` is removed (CORE-03). It had no effect unless larger than `MAX_RETRIES`. Rollback now starts when `MAX_RETRIES` retries are exhausted. A leftover `ROLLBACK_THRESHOLD` is ignored with a deprecation warning, and the `rollback_threshold` field is gone from `GET /config` and from the Terraform `orchestrator` module (remove it from your `.tfvars` if you set it).

### Known issues

These are confirmed by the current code and are tracked in [Data Flow: Known Behaviours](../architecture/data-flow.md#known-behaviours-and-limitations):

- The first failure webhook for a deployment returns `alert` instead of `retry`, because `workflow_run_id` from earlier webhooks is not stored in the new state. Two orchestrator unit tests fail for this reason.
- `GET /metrics/deployment/{namespace}/{deployment_name}` is shadowed by the Prometheus mount and returns Prometheus text.
- Metrics `deployment_duration_seconds` and `active_deployments` are declared but never updated; several alerts reference metrics the orchestrator does not emit.
- `LOG_LEVEL` and `ENABLE_AUDIT_LOGGING` are parsed but not applied.
- No authentication on the webhook and API endpoints; Grafana ships with a default password.
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

There is no upgrade path to document yet because no releases have been published. As a general rule:

- Redis state expires after 24 hours, so restarting the orchestrator or upgrading it does not require a migration.
- `last_successful:<namespace>:<app>` keys do not expire; keep them if you want rollbacks to work immediately after an upgrade.
- Configuration is read at startup. Restart the orchestrator after changing environment variables.
