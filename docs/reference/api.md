# API Reference

The orchestrator exposes a JSON-over-HTTP API implemented in `orchestrator/src/api.py` with FastAPI. Interactive documentation is generated automatically at `/docs` (Swagger UI) and `/redoc`, and the OpenAPI schema is at `/openapi.json`.

**Base URL.** In-cluster: `http://orchestrator-service.orchestrator.svc:8000`. Locally:

```bash
kubectl port-forward -n orchestrator svc/orchestrator-service 8000:8000
export MANA=http://localhost:8000
```

## Authentication

| Endpoints | Credential |
| --------- | ---------- |
| `POST /webhook/deployment` | `X-Mana-Timestamp` (Unix seconds) and `X-Mana-Signature`: `sha256=` followed by the hex HMAC-SHA256 of `<timestamp>.<raw body>` with `WEBHOOK_SECRET`. The timestamp must be within 300 seconds of the server clock |
| `POST /rollback`, `POST /health-check`, `GET /deployment/{id}`, `GET /deployments/recent`, `GET /deployments/{ns}/{name}/status`, `DELETE /denied-versions/{ns}/{name}/{version}`, `GET /config` | `X-API-Key` equal to one of the configured `API_KEY` values |
| `GET /`, `GET /health`, `GET /livez`, `GET /readyz`, `GET /metrics`, `/docs`, `/redoc`, `/openapi.json` | none (probes and Prometheus; restrict them with the NetworkPolicy) |

A missing or wrong credential returns `401` with a generic body. The signature is checked before the payload, so a forged request never reaches validation. `WEBHOOK_SECRET` and `API_KEY` accept comma-separated lists: add the new value, update the clients, then remove the old one. A captured, valid webhook can be replayed for up to five minutes; idempotency keys are planned (CORE-06).

`scripts/notify-orchestrator.sh` builds, signs and sends a webhook from environment variables, and is what the workflows use:

```bash
export ORCHESTRATOR_URL=$MANA
export MANA_WEBHOOK_SECRET=$(kubectl -n orchestrator get secret orchestrator-secrets -o jsonpath='{.data.webhook-secret}' | base64 -d)
export MANA_API_KEY=$(kubectl -n orchestrator get secret orchestrator-secrets -o jsonpath='{.data.api-key}' | base64 -d)
```

The examples below use `$MANA_API_KEY` for operator endpoints and show the webhook payload; send it with the script (`MANA_DEPLOYMENT_ID`, `MANA_NAMESPACE`, `MANA_VERSION`, `MANA_STATUS`, `MANA_FAILURE_TYPE`, `MANA_WORKFLOW_RUN_ID`). With `AUTH_DISABLED=true` (local development only) no credentials are needed.

## Conventions

- Request and response bodies are `application/json`.
- Validation errors return HTTP `422` in FastAPI's standard shape (`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`).
- Unexpected errors return HTTP `500` with `{"detail": "<message>"}`.
- Timestamps in deployment records are Unix epoch seconds (float).

## Endpoint Summary

| Method | Path                                                | Purpose                                                                                           |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `GET`  | `/`                                                 | Service info and feature list                                                                     |
| `GET`  | `/livez`                                            | Liveness probe (process only)                                                     |
| `GET`  | `/readyz`                                           | Readiness probe (needs Redis)                                                     |
| `GET`  | `/health`                                           | Dependency report: Redis and Kubernetes                                                           |
| `POST` | `/webhook/deployment`                               | Receive deployment events from CI/CD                                                              |
| `POST` | `/rollback`                                         | Manually roll a Deployment back to a version                                                      |
| `POST` | `/health-check`                                     | Run a deployment health check on demand                                                           |
| `GET`  | `/deployment/{deployment_id}`                       | Fetch one stored deployment state                                                                 |
| `GET`  | `/deployments/recent`                               | List recent deployment states                                                                     |
| `GET`  | `/deployments/{namespace}/{deployment_name}/status` | Replica counts and conditions                                                                     |
| `DELETE` | `/denied-versions/{namespace}/{deployment_name}/{version}` | Take a version off the deny-list                                                         |
| `GET`  | `/config`                                           | Effective orchestrator configuration                                                              |
| `GET`  | `/metrics`                                          | Prometheus metrics                                                                                |

---

## `GET /`

Returns static service information.

```bash
curl -s $MANA/
```

```json
{
  "service": "Self-Healing Orchestrator",
  "version": "1.0.0",
  "status": "healthy",
  "features": [
    "Application health endpoint monitoring",
    "Exponential backoff for retries",
    "GitHub Actions retry integration",
    "Comprehensive audit logging",
    "Automatic rollback on failure",
    "Previous version tracking"
  ],
  "github_integration": true
}
```

`github_integration` is `true` only when both `GITHUB_TOKEN` and `GITHUB_REPO` are set.

## `GET /livez`

Returns `{"status": "alive"}` while the process and its event loop respond. It never touches Redis or the API server, so a dependency outage cannot make Kubernetes restart a healthy pod. Used for the container `HEALTHCHECK`, the startup probe and the liveness probe.

## `GET /readyz`

Returns `{"status": "ready"}` when Redis answers, which holds all state and the job queue. Answers `503` with `{"status": "not_ready", "redis": "unhealthy"}` when it does not, and `503` with `{"status": "draining"}` while the replica shuts down. Kubernetes uses it as the readiness probe, so a replica without Redis, or one that is draining, stops receiving webhooks. It does not check Kubernetes or GitHub.

## `GET /health`

Reports Redis (`PING`) and Kubernetes (the version endpoint, which needs no cluster-scope permission). HTTP 200 when both are healthy and **503** when either is not; the body is the same in both cases. Use it for humans and monitoring, not for probes.

```json
{
  "status": "healthy",
  "redis": "healthy",
  "kubernetes": "healthy",
  "github_integration": true
}
```

`status` is `healthy` when both dependencies are healthy, otherwise `degraded`.

---

## `POST /webhook/deployment`

The entry point for CI/CD events. See [Data Flow](../architecture/data-flow.md) for what happens next.

### Request body

| Field           | Type   | Required             | Description                                                                                           |
| --------------- | ------ | -------------------- | ----------------------------------------------------------------------------------------------------- |
| `deployment_id` | string | yes                  | Unique ID for this deployment. Retry state is keyed on it                                             |
| `namespace`     | string | yes                  | Kubernetes namespace of the target                                                                    |
| `app_name`      | string | yes                  | Deployment name (also the `app` label and container name)                                             |
| `version`       | string | yes                  | Version or image tag being deployed                                                                   |
| `status`        | string | yes                  | `in_progress`, `success` or `failed`. Other values are rejected with HTTP 422                          |
| `failure_type`  | string | when `status=failed` | One of `build_failure`, `test_failure`, `deployment_failure`, `health_check_failure`, `smoke_test_failure`, `timeout` |
| `metadata`      | object | no                   | Free-form. Include `workflow_run_id` (string) for retries. Typical keys: `commit`, `author`, `branch` |

```bash
# Unsigned example of the body; sign it as described under Authentication
curl -s -X POST $MANA/webhook/deployment \
  -H 'Content-Type: application/json' \
  -H "X-Mana-Timestamp: $TS" -H "X-Mana-Signature: $SIGNATURE" \
  -d '{
    "deployment_id": "deploy-123",
    "namespace": "production",
    "app_name": "sample-app",
    "version": "v1a2b3c4-1760000000",
    "status": "failed",
    "failure_type": "health_check_failure",
    "metadata": {"commit": "1a2b3c4", "branch": "main", "workflow_run_id": "9876543210"}
  }'
```

### Responses

**`status=failed`** (HTTP 200). `action_taken` depends on the decision:

```json
{
  "status": "processed",
  "deployment_id": "deploy-123",
  "action_taken": {
    "action": "retry",
    "retry_count": 1,
    "backoff_seconds": 10.0,
    "workflow_run_id": "9876543210",
    "message": "Retry 1/1 scheduled in 10.0s"
  }
}
```

```json
{
  "status": "processed",
  "deployment_id": "deploy-123",
  "action_taken": {
    "action": "rollback",
    "previous_version": "v0f9e8d7-1759990000",
    "message": "Rollback to version v0f9e8d7-1759990000 scheduled"
  }
}
```

```json
{
  "status": "processed",
  "deployment_id": "deploy-123",
  "action_taken": {
    "action": "alert",
    "message": "No previous version available - manual intervention required"
  }
}
```

| `action`   | Meaning                                                                                      | Other fields                                                   |
| ---------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `retry`    | A workflow re-run has been scheduled after the backoff                                       | `retry_count`, `backoff_seconds`, `workflow_run_id`, `message` |
| `rollback` | Rollback **scheduled**; it runs as a durable job and the state becomes `rolled_back` (or `failed`) when it finishes | `previous_version`, `message`                                  |
| `skip`     | The failed version is not the one running, so rolling back would change a healthy Deployment | `live_version`, `message`                                      |
| `alert`    | Cannot recover automatically (no `workflow_run_id`, no previous version, a build or test failure, the live version could not be checked, or rollback failed) | `message` |

**`status=success`** (HTTP 200):

```json
{
  "status": "success",
  "deployment_id": "deploy-123",
  "message": "Deployment successful - version saved for future rollbacks"
}
```

Where [post-deploy verification](../configuration/recovery-policy.md#post-deploy-verification) applies to the namespace, a `success` event does not settle the deployment. The answer is instead (HTTP 200):

```json
{
  "status": "verifying",
  "deployment_id": "deploy-123",
  "message": "Deployment healthy - verifying against Prometheus for up to 180s before it is saved as a rollback target",
  "bake_seconds": 180
}
```

The deployment stays `verifying` until its checks pass (then it becomes `success` and the version is saved), or fail (then the policy handles a `verification_failure`, a rollback by default). A repeated `success` event for a deployment that is verifying changes nothing.

**`status=in_progress`** (HTTP 200): `{"status": "acknowledged", "deployment_id": "deploy-123"}`.

When the version was rolled back earlier and is on the deny-list, the answer is `{"status": "denied", "deployment_id": ..., "message": ..., "reason": "rolled back after health_check_failure", "denied_since": <unix time>}` (HTTP 200). Nothing is stored for the refused deployment. `scripts/notify-orchestrator.sh` exits 3 on it and the deploy workflow stops before deploying. See [Recovery Policy](../configuration/recovery-policy.md#circuit-breaker-and-deny-list).

### Errors

| Code  | Cause                                                                                                                                       |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `422` | Missing or mistyped required field, a `status` other than `in_progress`, `success` or `failed`, an unknown `failure_type`, or `status: failed` without a `failure_type` |
| `409` | An identical signed request is still being processed (see below)                                                                            |
| `500` | An internal error such as Redis being unavailable                                                                                           |

### Duplicate deliveries

A signed request is identified by its signature. If the same request arrives again within ten minutes, whether replayed, retried by `curl` or delivered to the other replica, MANA does not process it again: it answers with the first response and the header `X-Mana-Duplicate: true` (counted in `mana_webhook_duplicates_total{outcome="replayed"}`). A copy that arrives while the first is still running gets `409` (`outcome="in_flight"`). A request whose processing failed with a 5xx is released, so the sender can retry it. Two different events are never merged: each signed request has its own timestamp, and `scripts/notify-orchestrator.sh` adds a random `metadata.delivery_id`. Without a signature (`AUTH_DISABLED=true`) every request is processed.

!!! note
The webhook answers as soon as the decision is saved and the job is queued, so the response time no longer depends on the rollback. To follow a rollback, poll `GET /deployment/{id}` until `status` is `rolled_back` or `failed`.

---

## `POST /rollback`

Rolls a Deployment back to a specific version without any retry logic. The image tag of the application container (named `deployment_name`, or the only container) is replaced and the `version` labels are updated, then health is verified.

| Field             | Type   | Required | Description             |
| ----------------- | ------ | -------- | ----------------------- |
| `namespace`       | string | yes      | Target namespace        |
| `deployment_name` | string | yes      | Deployment to roll back |
| `target_version`  | string | yes      | Image tag to restore    |

```bash
curl -s -X POST $MANA/rollback \
  -H "X-API-Key: $MANA_API_KEY" -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","target_version":"v0f9e8d7-1759990000"}'
```

```json
{
  "status": "success",
  "deployment_id": "manual-rollback-6c1f0c0e-6f58-4a8b-9d5f-0d7a3e1b2c44",
  "message": "Rolled back to version v0f9e8d7-1759990000"
}
```

Returns `409` if another rollback of the same Deployment is running (the lock is shared with the automatic rollbacks), and `500` if the rollback does not become healthy. The body is `{"detail": "500: Rollback failed"}` (the prefix is an artefact of the endpoint re-wrapping its own `HTTPException`). A successful call increments `rollbacks_total{reason="manual"}`. Manual rollbacks are logged under the generated `manual-rollback-…` ID but do not create a stored state record.

## `POST /health-check`

Runs the deployment health check (all replicas ready plus application health) and records its duration.

| Field             | Type    | Required | Default | Description             |
| ----------------- | ------- | -------- | ------- | ----------------------- |
| `namespace`       | string  | yes      |         | Target namespace        |
| `deployment_name` | string  | yes      |         | Deployment to check     |
| `timeout`         | integer | no       | `300`   | Maximum seconds to wait |

```bash
curl -s -X POST $MANA/health-check -H "X-API-Key: $MANA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","timeout":60}'
```

```json
{
  "deployment_name": "sample-app",
  "namespace": "production",
  "healthy": true,
  "check_duration_seconds": 3.4
}
```

The call blocks until the check passes, fails or times out.

---

## `GET /deployment/{deployment_id}`

Returns the stored state for one deployment, or `404` (`{"detail": "Deployment not found"}`) if there is none or it has expired (24 h TTL).

```bash
curl -s -H "X-API-Key: $MANA_API_KEY" $MANA/deployment/deploy-123
```

```json
{
  "deployment_id": "deploy-123",
  "namespace": "production",
  "app_name": "sample-app",
  "version": "v1a2b3c4-1760000000",
  "status": "rolled_back",
  "retry_count": 4,
  "attempts": { "health_check_failure": 4 },
  "failure_type": "health_check_failure",
  "previous_version": "v0f9e8d7-1759990000",
  "timestamp": 1760000123.45,
  "started_at": 1760000000.12,
  "first_failure_at": 1760000060.5,
  "finished_at": 1760000123.45,
  "metadata": { "workflow_run_id": "9876543210" }
}
```

`started_at`, `first_failure_at` and `finished_at` are Unix timestamps, or `null` when the event was never seen (for example `started_at` for a deployment whose first webhook was a failure).

`status` is one of `pending`, `in_progress`, `success`, `failed`, `rolling_back`, `rolled_back`, `retry_scheduled`, `verifying`.

## `GET /deployments/recent`

| Query parameter | Type    | Default | Description                        |
| --------------- | ------- | ------- | ---------------------------------- |
| `namespace`     | string  | all     | Only deployments in this namespace |
| `limit`         | integer | `10`    | Maximum results                    |

```bash
curl -s -H "X-API-Key: $MANA_API_KEY" "$MANA/deployments/recent?namespace=production&limit=5"
```

```json
{
  "count": 1,
  "deployments": [
    {
      "deployment_id": "deploy-123",
      "namespace": "production",
      "app_name": "sample-app",
      "version": "v1a2b3c4-1760000000",
      "status": "rolled_back",
      "retry_count": 4,
      "timestamp": 1760000123.45
    }
  ]
}
```

Results are sorted newest first. The list is built by scanning all `deployment:*` keys in Redis, so it is intended for dashboards and debugging rather than high-frequency polling.

## `GET /deployments/{namespace}/{deployment_name}/status`

Returns live replica information straight from Kubernetes. This replaces `/metrics/deployment/...`, which was unreachable because the Prometheus app was mounted over it and now returns `404`:

```json
{
  "replicas": 3,
  "ready_replicas": 3,
  "available_replicas": 3,
  "unavailable_replicas": 0,
  "conditions": [
    {
      "type": "Available",
      "status": "True",
      "reason": "MinimumReplicasAvailable"
    }
  ]
}
```

## `GET /config`

Shows the values the running process is using, which is the fastest way to confirm a configuration change took effect.

```json
{
  "max_retries": 3,
  "initial_backoff": 10.0,
  "max_backoff": 300.0,
  "backoff_multiplier": 2.0,
  "health_check_timeout": 5,
  "max_app_health_failures": 3,
  "github_integration_enabled": true
}
```

## `GET /metrics`

Prometheus text exposition format, served directly (no redirect). Rendering reads the active-deployment count and the job queue depth from Redis, so a scrape omits those gauges while Redis is unreachable.

### Application metrics

| Metric                               | Type      | Labels                       | Updated                                                                                                      |
| ------------------------------------ | --------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `deployments_total`                  | counter   | `namespace`, `status`        | Deployments reaching a final status (`success`, `failed`, `rolled_back`), once per transition; `in_progress` and retried attempts are not counted |
| `deployment_duration_seconds`        | histogram | `namespace`, `status`        | First `in_progress` event to the final status, across all attempts                                           |
| `mana_recovery_duration_seconds`     | histogram | `namespace`, `app`, `outcome` | First failure to a successful retry (`retry_succeeded`) or a verified rollback (`rolled_back`)               |
| `rollback_decision_duration_seconds` | histogram |                              | Time to decide retry, rollback, skip or alert for a failure (excludes the rollback itself)                   |
| `active_deployments`                 | gauge     | `namespace`                  | Deployments that are `in_progress`, `retry_scheduled`, `rolling_back` or `verifying`, read from Redis at scrape time. Every replica reports the same value: aggregate with `max by (namespace)`, not `sum` |
| `retries_total`                      | counter   | `namespace`, `retry_attempt` | Retries scheduled                                                                                            |
| `rollbacks_total`                    | counter   | `namespace`, `reason`        | Successful rollbacks (`reason` is the failure type, or `manual`)                                             |
| `health_checks_total`                | counter   | `namespace`, `result`        | Deployment health checks (`success` or `failed`), including those run during a rollback                     |
| `health_check_duration_seconds`      | histogram | `namespace`, `deployment`    | Duration of each deployment health check                                                                     |
| `webhook_requests_total`             | counter   | `endpoint`, `code`           | Requests to `/webhook/*` by HTTP status                                                                      |
| `webhook_errors_total`               | counter   | `endpoint`, `reason`         | Webhook requests answered with an error (`validation` for 422, `server_error` for 5xx, `client_error`)      |
| `redis_connection_errors_total`      | counter   |                              | Redis calls (including queue scripts) that failed with a connection or timeout error                         |
| `mana_scheduler_pending_jobs`        | gauge     |                              | Jobs waiting to run, read from Redis at scrape time (use `max`)                                              |
| `mana_scheduler_inflight_jobs`       | gauge     |                              | Jobs claimed by a worker                                                                                     |
| `mana_scheduler_overdue_seconds`     | gauge     |                              | How long the oldest due job has waited; grows when no replica works the queue                                |
| `mana_scheduler_jobs_total`          | counter   | `kind`, `outcome`            | Job executions: `completed`, `retried`, `deferred`, `dead`, `lease_expired`                                  |
| `mana_github_api_requests_total`     | counter   | `endpoint`, `code`           | GitHub calls (`rerun`, `status`) by HTTP status, or `error` when none came back                              |
| `mana_alerts_sent_total`             | counter   | `channel`, `outcome`         | Alert deliveries: `sent`, `skipped` (no commit for a status), `failed`                                       |
| `mana_webhook_duplicates_total`      | counter   | `outcome`                    | Webhook copies dropped: `replayed` or `in_flight`                                                            |

A failure for a deployment that is already `failed` (for example a fifth failure webhook) is not counted again, and a re-run that reopens a finished deployment and settles again is counted again. Build and test failures reported with `namespace: ci` are counted as `failed` in that namespace (CORE-14).

The standard `python_*` and `process_*` collector metrics are also exported. See [Monitoring](../usage/monitoring.md) for queries and dashboards.

---

## Using the API from GitHub Actions

The shipped workflows call `scripts/notify-orchestrator.sh` with everything in `env:`, so no secret or input is spliced into a shell script:

```yaml
- name: Notify MANA
  if: always() && env.ORCHESTRATOR_URL != ''
  continue-on-error: true
  env:
    MANA_WEBHOOK_SECRET: ${{ secrets.MANA_WEBHOOK_SECRET }}
    MANA_DEPLOYMENT_ID: ${{ steps.identity.outputs.deployment_id }}
    MANA_NAMESPACE: production
    MANA_VERSION: ${{ inputs.version }}
    MANA_STATUS: ${{ steps.status.outputs.status }}
    MANA_FAILURE_TYPE: ${{ steps.status.outputs.failure_type }}
    MANA_WORKFLOW_RUN_ID: ${{ github.run_id }}
  run: scripts/notify-orchestrator.sh
```

The script builds the JSON with `jq`, signs it, validates `status` and `failure_type`, and sets a timeout longer than the rollback health wait. See [CI/CD Integration](../usage/cicd-integration.md) for the full setup.

---

## `DELETE /denied-versions/{namespace}/{deployment_name}/{version}`

Requires `X-API-Key`. Takes a version off the deny-list so a pipeline may deploy it again. A version is denied after a verified rollback (see the [recovery policy](../configuration/recovery-policy.md#circuit-breaker-and-deny-list)).

```bash
curl -s -X DELETE -H "X-API-Key: $MANA_API_KEY" $MANA/denied-versions/production/sample-app/v1a2b3c4
```

Answers `200` with `{"status": "allowed", ...}`, `404` when the version is not on the list, and `503` when Redis is unavailable.

## Automation state in `GET /deployments/{namespace}/{deployment_name}/status`

The replica status is followed by an `automation` object:

```json
{
  "automation": {
    "circuit_breaker": {"open": false, "rollbacks_in_window": 1, "max_rollbacks": 3, "window_seconds": 3600},
    "denied_versions": [
      {"version": "v1a2b3c4", "since": 1790000000.0, "expires_at": 1790604800.0,
       "reason": "rolled back after health_check_failure", "deployment_id": "deploy-123-production"}
    ]
  }
}
```

`GET /deployment/{id}` also returns `diagnosis` (for example `CrashLoopBackOff`, or `null`), `attempts` (failures per failure type, which the policy counts; `retry_count` is the total) and, for a deployment that went through verification, `verification` (`started_at`, `evaluations`, the breach `streaks` per check and what each check `last` saw). `GET /config` includes a `policy` block: the file, the reload interval, when it was last loaded and whether verification is active.
