# API Reference

The orchestrator exposes a JSON-over-HTTP API implemented in `orchestrator/src/api.py` with FastAPI. Interactive documentation is generated automatically at `/docs` (Swagger UI) and `/redoc`, and the OpenAPI schema is at `/openapi.json`.

**Base URL.** In-cluster: `http://orchestrator-service.orchestrator.svc:8000`. Locally:

```bash
kubectl port-forward -n orchestrator svc/orchestrator-service 8000:8000
export MANA=http://localhost:8000
```

!!! danger "No authentication"
The API has **no authentication or authorization**. Anyone who can reach it can trigger rollbacks (`POST /rollback`) and submit forged webhooks. Keep it on a private network, restrict access with NetworkPolicies, or put it behind an authenticating proxy. See [Security](../about/security.md).

## Conventions

- Request and response bodies are `application/json`.
- Validation errors return HTTP `422` in FastAPI's standard shape (`{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`).
- Unexpected errors return HTTP `500` with `{"detail": "<message>"}`.
- Timestamps in deployment records are Unix epoch seconds (float).

## Endpoint Summary

| Method | Path                                                | Purpose                                                                                           |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `GET`  | `/`                                                 | Service info and feature list                                                                     |
| `GET`  | `/health`                                           | Liveness and dependency health                                                                    |
| `POST` | `/webhook/deployment`                               | Receive deployment events from CI/CD                                                              |
| `POST` | `/rollback`                                         | Manually roll a Deployment back to a version                                                      |
| `POST` | `/health-check`                                     | Run a deployment health check on demand                                                           |
| `GET`  | `/deployment/{deployment_id}`                       | Fetch one stored deployment state                                                                 |
| `GET`  | `/deployments/recent`                               | List recent deployment states                                                                     |
| `GET`  | `/metrics/deployment/{namespace}/{deployment_name}` | Replica counts and conditions (see [known issue](#get-metricsdeploymentnamespacedeployment_name)) |
| `GET`  | `/config`                                           | Effective orchestrator configuration                                                              |
| `GET`  | `/metrics/`                                         | Prometheus metrics                                                                                |

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

## `GET /health`

Checks Redis (`PING`) and Kubernetes (`list_namespace(limit=1)`). It always returns HTTP 200; inspect the body for status. This is the endpoint used by the container and Kubernetes probes.

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
curl -s -X POST $MANA/webhook/deployment \
  -H 'Content-Type: application/json' \
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
    "message": "Retry 1/3 scheduled in 10.0s"
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
    "message": "Rolled back to version v0f9e8d7-1759990000"
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
| `rollback` | Rollback executed and verified healthy                                                       | `previous_version`, `message`                                  |
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

**`status=in_progress`** (HTTP 200): `{"status": "acknowledged", "deployment_id": "deploy-123"}`

### Errors

| Code  | Cause                                                                                                                                       |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `422` | Missing or mistyped required field, a `status` other than `in_progress`, `success` or `failed`, an unknown `failure_type`, or `status: failed` without a `failure_type` |
| `500` | An internal error such as Redis being unavailable                                                                                           |

!!! note
The rollback branch runs synchronously inside the request, including the health verification wait (up to 300 s). Configure your CI client's HTTP timeout accordingly, or treat a timeout as "processing" and check `GET /deployment/{id}` afterwards.

---

## `POST /rollback`

Rolls a Deployment back to a specific version without any retry logic. The image tag of the container named `deployment_name` is replaced and the `version` labels are updated, then health is verified.

| Field             | Type   | Required | Description             |
| ----------------- | ------ | -------- | ----------------------- |
| `namespace`       | string | yes      | Target namespace        |
| `deployment_name` | string | yes      | Deployment to roll back |
| `target_version`  | string | yes      | Image tag to restore    |

```bash
curl -s -X POST $MANA/rollback \
  -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","target_version":"v0f9e8d7-1759990000"}'
```

```json
{
  "status": "success",
  "deployment_id": "manual-rollback-6c1f0c0e-6f58-4a8b-9d5f-0d7a3e1b2c44",
  "message": "Rolled back to version v0f9e8d7-1759990000"
}
```

Returns `500` if the rollback does not become healthy. The body is `{"detail": "500: Rollback failed"}` (the prefix is an artefact of the endpoint re-wrapping its own `HTTPException`). A successful call increments `rollbacks_total{reason="manual"}`. Manual rollbacks are logged under the generated `manual-rollback-…` ID but do not create a stored state record.

## `POST /health-check`

Runs the deployment health check (all replicas ready plus application health) and records its duration.

| Field             | Type    | Required | Default | Description             |
| ----------------- | ------- | -------- | ------- | ----------------------- |
| `namespace`       | string  | yes      |         | Target namespace        |
| `deployment_name` | string  | yes      |         | Deployment to check     |
| `timeout`         | integer | no       | `300`   | Maximum seconds to wait |

```bash
curl -s -X POST $MANA/health-check \
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
curl -s $MANA/deployment/deploy-123
```

```json
{
  "deployment_id": "deploy-123",
  "namespace": "production",
  "app_name": "sample-app",
  "version": "v1a2b3c4-1760000000",
  "status": "rolled_back",
  "retry_count": 4,
  "failure_type": "health_check_failure",
  "previous_version": "v0f9e8d7-1759990000",
  "timestamp": 1760000123.45,
  "metadata": { "workflow_run_id": "9876543210" }
}
```

`status` is one of `pending`, `in_progress`, `success`, `failed`, `rolling_back`, `rolled_back`, `retry_scheduled`.

## `GET /deployments/recent`

| Query parameter | Type    | Default | Description                        |
| --------------- | ------- | ------- | ---------------------------------- |
| `namespace`     | string  | all     | Only deployments in this namespace |
| `limit`         | integer | `10`    | Maximum results                    |

```bash
curl -s "$MANA/deployments/recent?namespace=production&limit=5"
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

## `GET /metrics/deployment/{namespace}/{deployment_name}`

Intended to return live replica information straight from Kubernetes:

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

!!! warning "Known issue: route shadowed by the Prometheus mount"
The Prometheus ASGI app is mounted at `/metrics` before this route is registered, so requests to `/metrics/deployment/<ns>/<name>` are handled by the Prometheus app and return Prometheus text instead of the JSON above. Until the route is moved (for example to `/deployment-metrics/{namespace}/{name}`), use `kubectl get deployment <name> -n <ns> -o json` or the metrics endpoint for the same information.

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

## `GET /metrics/`

Prometheus text exposition format. A request to `/metrics` (without the trailing slash) returns a `307` redirect to `/metrics/`; Prometheus follows redirects, so scraping works with either form.

### Application metrics

| Metric                          | Type      | Labels                       | Updated                                                             |
| ------------------------------- | --------- | ---------------------------- | ------------------------------------------------------------------- |
| `deployments_total`             | counter   | `namespace`, `status`        | every webhook                                                       |
| `retries_total`                 | counter   | `namespace`, `retry_attempt` | when a retry is scheduled                                           |
| `rollbacks_total`               | counter   | `namespace`, `reason`        | on a successful rollback (`reason` is the failure type or `manual`) |
| `health_check_duration_seconds` | histogram | `namespace`, `deployment`    | `POST /health-check`                                                |
| `deployment_duration_seconds`   | histogram | `namespace`                  | declared, **never observed**                                        |
| `active_deployments`            | gauge     | `namespace`                  | declared, **never set**                                             |

The standard `python_*` and `process_*` collector metrics are also exported. See [Monitoring](../usage/monitoring.md) for queries and dashboards.

---

## Using the API from GitHub Actions

The shipped workflows send webhooks with `curl`. A minimal reusable step:

```yaml
- name: Notify MANA
  if: always() && env.ORCHESTRATOR_URL != ''
  run: |
    curl -sS --fail-with-body --max-time 330 -X POST "$ORCHESTRATOR_URL/webhook/deployment" \
      -H "Content-Type: application/json" \
      -d "$(jq -n \
        --arg id  "$DEPLOYMENT_ID" --arg ns "production" --arg app "sample-app" \
        --arg ver "$VERSION" --arg st "${{ job.status == 'success' && 'success' || 'failed' }}" \
        --arg run "${{ github.run_id }}" \
        '{deployment_id:$id, namespace:$ns, app_name:$app, version:$ver, status:$st,
          failure_type:(if $st=="failed" then "deployment_failure" else null end),
          metadata:{workflow_run_id:$run}}')"
```

Using `jq` to build the JSON avoids quoting bugs when values contain special characters. See [CI/CD Integration](../usage/cicd-integration.md) for the full setup.
