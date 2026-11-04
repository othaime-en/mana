# Architecture Overview

MANA (Self-Healing CI/CD Pipeline) is a Kubernetes-native orchestration service that reacts to deployment events from a CI/CD pipeline and automatically recovers from failures by retrying the pipeline and, when that does not help, rolling the Deployment back to its last known-good version.

This page explains the design at a high level. Details are in [Components](components.md) and [Data Flow](data-flow.md).

## Design Principles

- **Event-driven.** The pipeline reports deployment events by webhook. The orchestrator reacts to them; it does not poll the cluster for new Deployments.
- **Stateful decisions.** Whether to retry or roll back depends on history (how many times this deployment has failed and what the last good version was), so state lives in Redis.
- **Verify, do not assume.** A rollback is only reported as successful after Kubernetes shows all replicas ready **and** the application's own `/health` endpoint answers.
- **Auditable.** Every decision and action produces a structured audit event.
- **Simple, replaceable pieces.** One small FastAPI service, Redis, and the Kubernetes and GitHub APIs. No custom resource definitions or operators.

## High-Level Architecture

![MANA high-level architecture: GitHub Actions sends webhooks to the orchestrator, which calls the Kubernetes API, stores state in Redis, asks GitHub to re-run workflows and sends metrics and logs to the monitoring stack, which alerts Slack, PagerDuty or email](../assets/diagrams/high-level-architecture.svg)

## Core Components

| Component                  | Responsibility                                                                                  | Details                                                            |
| -------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Orchestrator**           | Receives webhooks, decides retry, rollback or alert, executes recovery, exposes API and metrics | [Components: Orchestrator](components.md#orchestrator)             |
| **State store (Redis)**    | Deployment state (24 h TTL) and the last successful version per app                             | [Components: State Store](components.md#state-store-redis)         |
| **Kubernetes integration** | Reads Deployments, ReplicaSets and Pods; patches the Deployment image on rollback               | uses the official Python `kubernetes` client                       |
| **Health checker**         | Replica readiness plus an HTTP call to each pod's health endpoint                               | [Data Flow: Health verification](data-flow.md#health-verification) |
| **Decision engine**        | Retry counting, exponential backoff, rollback rule                                              | [Data Flow: The decision rule](data-flow.md#the-decision-rule)     |
| **GitHub integration**     | Asks GitHub to re-run a failed workflow run                                                     | [CI/CD Integration](../usage/cicd-integration.md)                  |
| **Audit logger**           | One JSON audit event per action                                                                 | [Monitoring](../usage/monitoring.md#structured-logging)            |
| **Sample app**             | Flask service used as a deployment target, with failure simulation                              | [Components: Sample Application](components.md#sample-application) |
| **Monitoring stack**       | Prometheus, Grafana, Loki/Alloy, Alertmanager configuration                                  | [Monitoring](../usage/monitoring.md)                               |

## How a Deployment Is Handled

1. The pipeline deploys a new version and posts `POST /webhook/deployment` with `status` of `in_progress`, `success` or `failed`.
2. On **success**, the orchestrator records the version as the new rollback target.
3. On **failure**, it increments the failure count for that deployment ID, diagnoses the cause from the pods, and applies the [recovery policy](../configuration/recovery-policy.md) for the failure type:
   - **retry**: schedule a GitHub Actions re-run after an exponential backoff (for failures worth retrying, up to the rule's attempts and `MAX_RETRIES`);
   - **rollback**: if a previous version is known and the failed version is the one running, patch the Deployment back to the last good version and verify health. Post-deploy failures such as a failed health check take this path on the first failure;
   - **alert**: raise `manual_intervention_required` when the policy says so, when no rollback is possible, or when the circuit breaker is open.

   A watcher can also detect stuck rollouts from the cluster without a webhook, and a rolled-back version is denied for later deployments.
4. Every step is written to the audit log, and Prometheus counters are updated.

The step-by-step version, with the decision table, is in [Data Flow](data-flow.md).

### Retry path

![Retry path: a failure webhook loads or creates state, increments retry_count, computes the backoff and saves retry_scheduled; a background task waits and posts the GitHub re-run request; the re-run sends its own webhook, and success records the rollback target while failure repeats or rolls back](../assets/diagrams/flow-retry-path.svg)

### Rollback path

![Rollback path: when retry_count exceeds the limits, find the previous version, save rolling_back, patch the Deployment image and version labels, wait for replicas ready and app /health, then save rolled_back with a rollback_completed audit event or failed with rollback_failed](../assets/diagrams/flow-rollback-path.svg)

## Design Decisions

**Why webhooks instead of watching the cluster?** The pipeline already knows precisely when a deployment starts and how it failed, and it holds the context needed to retry (the workflow run). Watching would duplicate that and still could not re-run the pipeline.

**Why re-run the pipeline for retries?** A retry should repeat exactly what the pipeline did (build, push, deploy). Asking GitHub to re-run the workflow reuses all of that logic and keeps CI history accurate.

**Why roll back by patching the image tag?** It is explicit and auditable: the orchestrator sets the container image to the last version that reported success, rather than relying on Kubernetes revision history (`kubectl rollout undo`), which can point at a version that was never verified.

**Why Redis?** Retry counts and rollback targets need a small, fast, shared store with built-in expiry. Deployment records expire after 24 hours; rollback targets (`last_successful:*`) do not.

## Security Architecture

The orchestrator can modify Deployments, so its permissions and exposure matter. This section summarises what the code and manifests implement today; the full model, known gaps and a hardening checklist are on the [Security](../about/security.md) page.

**Implemented**

- **RBAC**: a Role and RoleBinding in each managed namespace (`production`, `staging`, `canary`): `get`/`patch` on Deployments, `list` on ReplicaSets and Pods. There are no cluster-wide permissions and it cannot read Secrets.
- **Container hardening**: non-root user (UID 1000), `allowPrivilegeEscalation: false`, all Linux capabilities dropped, CPU and memory limits.
- **Secrets**: the GitHub token comes from a Kubernetes Secret, never from an image or ConfigMap.
- **Network exposure**: Services are `ClusterIP`; nothing is exposed outside the cluster by default.
- **Input validation**: request bodies are validated by Pydantic models; configuration is range-checked at startup.
- **Audit trail**: structured events for every recovery action.

**Not implemented (be aware)**

- Webhooks are HMAC-signed and operator endpoints need an API key; `/health`, `/metrics` and the OpenAPI routes are open, so restrict ingress with the NetworkPolicy. A signed webhook can be replayed for 300 s until idempotency keys exist (CORE-06).
- Redis has no password or TLS support in the client.
- The RBAC role is cluster-wide.

## Scalability and Performance

- **Replicas.** The manifests run 2 orchestrator replicas that share state through Redis. There is **no leader election**, so if the same webhook reaches both replicas, both can act on it. Use one replica if you need strict single-actor behaviour.
- **Synchronous rollback.** A rollback runs inside the webhook request and waits for health verification (up to 300 s by default). Size your pipeline's HTTP timeout accordingly.
- **Background retries.** Retry waits run as in-memory background tasks in the replica that received the webhook.
- **Listing deployments.** `GET /deployments/recent` scans all `deployment:*` keys; it is meant for dashboards and debugging.
- **Footprint.** The orchestrator requests 100m CPU and 256Mi memory per replica.

## Failure Modes and Recovery

| Failure                                     | Behaviour                                                                                                                                                                                                           | Recovery                                                                                                                                 |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Orchestrator pod crash**                  | Kubernetes restarts it; the other replica keeps serving. State, queued retries and rollbacks are in Redis. A job the dead pod was running loses its lease (60 s by default) and another replica runs it; a half-finished rollback is resumed and verified | Automatic. Watch `mana_scheduler_overdue_seconds` and the `SchedulerStalled` alert if no replica is left |
| **Redis unavailable**                       | At startup the orchestrator exits (`Cannot connect to Redis`). At runtime `/readyz` answers 503, so Kubernetes stops sending webhooks to the replica, and `/health` reports `degraded`; failure webhooks that do arrive fail with HTTP 500 because state cannot be saved. The scheduler pauses and resumes from the queue | Restore Redis. The bundled Redis keeps its data on a persistent volume (about 1 s of writes at risk), so a restart keeps state, jobs and the `last_successful:*` rollback targets |
| **Kubernetes API unavailable**              | At startup the orchestrator exits. At runtime health checks fail and rollbacks fail (the response is an `alert` or an HTTP 500, depending on the error)                                                             | Restore the API server; send the failure again or call `POST /rollback`                                                                  |
| **GitHub API unavailable or token invalid** | The retry job is retried with backoff (a bad token or unknown run fails it at once); when it gives up the deployment is marked `failed` and an alert is sent. Rollbacks are unaffected                                                | Fix the token or wait; see `GitHubRerunFailing` and `JobsDeadLettered`                                                                    |
| **Application unhealthy after rollback**    | State becomes `failed` and a `manual_intervention_required` audit event is written; the API returns `alert`                                                                                                         | Investigate manually; check pod logs and events                                                                                          |
| **No previous version known**               | Decision is `alert`                                                                                                                                                                                                 | Ensure at least one successful deployment has been reported, or that pods carry a `version` label so the ReplicaSet fallback works       |
| **Duplicate webhook delivery**              | A copy of a signed request (replay, `curl` retry, the other replica) gets the first answer and is not processed again; two distinct events are both processed                                                         | None needed; see `mana_webhook_duplicates_total`                                                                                          |
| **Network partition**                       | Webhooks cannot arrive, or health checks to pod IPs time out and count as failures                                                                                                                                  | Heals automatically; a NetworkPolicy that blocks the orchestrator from pods or from `api.github.com` looks identical, so verify policies |

## Technology Stack

| Layer                  | Technology (as pinned in the repository)                                               |
| ---------------------- | -------------------------------------------------------------------------------------- |
| Orchestrator           | Python 3.12, FastAPI 0.126, Uvicorn 0.32, Pydantic 2.9                                 |
| Integrations           | `kubernetes` Python client 31, `redis` 5.2, `requests` 2.32, `prometheus-client` 0.24  |
| Sample app             | Python 3.12, Flask 3.0, Gunicorn 21.2, `prometheus-flask-exporter` 0.23                |
| State                  | Redis 8 (`redis:8-alpine`)                                                             |
| Containers             | Docker, non-root images based on `python:3.12-slim`                                    |
| Orchestration          | Kubernetes 1.23+                                                                       |
| Infrastructure as code | Terraform 1.5+, `hashicorp/kubernetes` ~> 2.23, `hashicorp/helm` ~> 2.11               |
| Monitoring             | kube-prometheus-stack (chart 54.0.0), Loki stack (chart 2.9.11), Grafana, Alertmanager |
| CI/CD                  | GitHub Actions, GHCR, Kind for pipeline smoke tests, Snyk, Trivy, Bandit               |
| Testing                | pytest, pytest-cov, pytest-asyncio                                                     |
| Documentation          | MkDocs with the `shadcn` theme                                                         |

## Extending MANA

MANA has no plugin framework; extension means changing the code. The natural extension points are:

- **New failure types**: add a value to `FailureType` in `orchestrator/src/orchestrator.py` and send it from your pipeline.
- **New decision rules**: edit `handle_deployment_failure()`; add a test for each branch.
- **Other CI systems**: the webhook half is CI-agnostic; implement an equivalent of `trigger_github_workflow_rerun()` for your system's re-run API.
- **Notifications**: alerts are routed by Alertmanager; add receivers in `monitoring/alertmanager/alertmanager.yml`. The orchestrator itself only logs `manual_intervention_required`.
- **Health strategies**: adjust `check_application_health()` for different response formats.
- **Other storage backends**: Redis is used directly through `redis_client`; replacing it means abstracting those calls.

See [Contributing](../contributing/contributing.md) for conventions.

## Known Limitations

Behaviours that differ from the intended design (re-run semantics, a shadowed metrics route, and some alerts without matching metrics) are listed with explanations on the [Data Flow](data-flow.md#known-behaviours-and-limitations) page.
