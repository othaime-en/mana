# Basic Usage Guide

This guide explains how to use MANA day to day once it is [installed](../getting-started/installation.md) and your pipeline is [sending webhooks](cicd-integration.md): how to see what it did, how to run health checks and rollbacks by hand, and how to read its logs.

```bash
kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000 &
export MANA=http://localhost:8000
```

In-cluster, use `http://orchestrator-service.orchestrator.svc:8000`.

## How MANA Works

MANA is **event-driven**, not a monitoring loop:

1. **Receive.** Your pipeline posts a webhook when a deployment starts, succeeds or fails.
2. **Record.** On success, the version is stored as the rollback target. On failure, the failure count for that deployment ID is incremented.
3. **Decide.** Retry (while the count is within `MAX_RETRIES`), roll back (once it exceeds the limits and a previous version exists), or alert.
4. **Act.** A retry asks GitHub to re-run the workflow after a backoff. A rollback patches the Deployment back to the last good image tag.
5. **Verify.** A rollback is only reported successful after all replicas are ready and each pod's health endpoint answers.
6. **Log.** Every step is written to the audit log and counted in Prometheus metrics.

MANA only acts on deployments named in webhooks. It does not discover or watch Deployments by namespace or label.

## Seeing What MANA Did

### Look up one deployment

State is stored per **deployment ID** (the ID your pipeline puts in the webhook), for 24 hours:

```bash
curl -s $MANA/deployment/<deployment-id> | jq
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

A `404` means the ID is unknown or older than 24 hours.

### List recent deployments

```bash
curl -s "$MANA/deployments/recent" | jq                         # last 10
curl -s "$MANA/deployments/recent?namespace=production&limit=20" | jq
```

Results are newest first and include `deployment_id`, `namespace`, `app_name`, `version`, `status`, `retry_count` and `timestamp`.

### Deployment statuses

| Status                   | Meaning                                                                                                     |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `retry_scheduled`        | A workflow re-run is queued after the backoff                                                               |
| `rolling_back`           | A rollback is in progress                                                                                   |
| `rolled_back`            | The Deployment was patched back to the previous version and is healthy                                      |
| `failed`                 | Recovery stopped: no `workflow_run_id`, no previous version, or the rollback was not healthy. Needs a human |
| `success`                | The pipeline reported success                                                                               |
| `pending`, `in_progress` | Defined but not currently written by the webhook handler                                                    |

Statuses are lowercase in the API.

### Check the live Deployment in Kubernetes

For replica counts and conditions, ask Kubernetes directly:

```bash
kubectl -n production get deployment sample-app
kubectl -n production rollout history deployment/sample-app
kubectl -n production get pods -l app=sample-app -o wide
```

!!! note
`GET /deployments/{namespace}/{name}/status` returns live replica counts and conditions; see the [API Reference](../reference/api.md#get-deploymentsnamespacedeployment_namestatus).

## Manual Operations

### Run a health check

Runs the same verification MANA uses after a rollback (all replicas ready, then each pod's `/health`). It blocks until the check passes, fails or times out.

```bash
curl -s -X POST $MANA/health-check -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","timeout":60}' | jq
```

```json
{
  "deployment_name": "sample-app",
  "namespace": "production",
  "healthy": true,
  "check_duration_seconds": 3.4
}
```

### Roll back to a specific version

```bash
curl -s -X POST $MANA/rollback -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","target_version":"v0f9e8d7-1759990000"}' | jq
```

```json
{
  "status": "success",
  "deployment_id": "manual-rollback-6c1f0c0e-6f58-4a8b-9d5f-0d7a3e1b2c44",
  "message": "Rolled back to version v0f9e8d7-1759990000"
}
```

The image tag of the application container (named like the Deployment, or the only container) is replaced, the `version` labels are updated, and health is verified. If the rollback is not healthy the call returns HTTP 500. Manual rollbacks are audited and counted (`rollbacks_total{reason="manual"}`) but do not create a stored deployment record. Use this when you want MANA's verification and audit trail instead of a plain `kubectl rollout undo`.

### Simulate a pipeline event

Useful for testing and demos (see the [Quick Start](../getting-started/quick-start.md)):

```bash
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' -d '{
  "deployment_id": "test-1", "namespace": "staging", "app_name": "sample-app",
  "version": "1.2.3", "status": "failed", "failure_type": "health_check_failure",
  "metadata": {"workflow_run_id": "1"}
}' | jq
```

!!! warning
Webhooks have real effects: enough failures for the same ID will patch the live Deployment. Test in a non-production namespace.

### Things there is no endpoint for

- **Marking a deployment successful.** Send a `success` webhook instead; it also records the version as the rollback target.
- **Triggering a retry directly.** Retries are triggered by failure webhooks. To re-run a pipeline by hand, use GitHub (**Actions, Re-run jobs**).
- **Listing monitored namespaces.** There is no such concept; MANA acts on what the webhooks name.

## Understanding Health Checks

Verification has two layers:

1. **Kubernetes readiness.** `ready_replicas` must equal the Deployment's `replicas`. Until then MANA keeps waiting (polling every 10 s).
2. **Application health.** MANA calls `GET http://<pod-ip>:<HEALTH_CHECK_PORT><HEALTH_CHECK_PATH>` on each running pod. A pod is healthy if it returns HTTP 200 with JSON containing `"status": "healthy"`. The Deployment passes if at least one pod is healthy.

If replicas are ready but the application check fails **3 consecutive times**, the verification fails early. Otherwise it gives up after 300 s (or the `timeout` you pass to `POST /health-check`). Details: [Data Flow](../architecture/data-flow.md#health-verification).

Test your application's endpoint the same way MANA does:

```bash
POD_IP=$(kubectl -n production get pod -l app=sample-app -o jsonpath='{.items[0].status.podIP}')
kubectl -n orchestrator exec deploy/orchestrator -- \
  python -c "import requests; r=requests.get('http://$POD_IP:5000/health', timeout=5); print(r.status_code, r.text)"
```

## Retries and Backoff

The delay before retry _n_ is `min(INITIAL_BACKOFF × BACKOFF_MULTIPLIER^(n-1), MAX_BACKOFF)`. With the defaults (10 s, ×2, cap 300 s):

| Retry       | Delay       |
| ----------- | ----------- |
| 1           | 10 s        |
| 2           | 20 s        |
| 3           | 40 s        |
| 4           | 80 s        |
| 5           | 160 s       |
| 6 and later | 300 s (cap) |

There is no jitter. With the default `MAX_RETRIES=3`, only the first three delays are used before a rollback becomes possible. Tune with [Orchestrator Configuration](../configuration/orchestrator.md#retry-and-rollback-policy).

## Reading the Audit Log

Audit events go to the orchestrator's standard output.

```bash
kubectl -n orchestrator logs -l app=orchestrator --tail=200
kubectl -n orchestrator logs -l app=orchestrator --since=1h
kubectl -n orchestrator logs -l app=orchestrator -f

# Only audit lines
kubectl -n orchestrator logs -l app=orchestrator | grep "AUDIT:"

# Everything about one deployment
kubectl -n orchestrator logs -l app=orchestrator | grep "<deployment-id>"
```

With the default log format, an audit line looks like:

```text
2026-02-05 14:30:00,123 - self-healing-orchestrator.audit - INFO - AUDIT: rollback_completed
```

The structured fields (deployment ID, namespace, version, `success`, details) are attached to the log record as `audit_event`. To see them as JSON in your log pipeline, use a JSON log formatter; see [Monitoring](monitoring.md#structured-logging).

### Audit actions

| Action                                                                 | Meaning                                            |
| ---------------------------------------------------------------------- | -------------------------------------------------- |
| `deployment_received`                                                  | A webhook arrived                                  |
| `failure_detected`                                                     | A failure was recorded                             |
| `retry_initiated`                                                      | A retry was scheduled                              |
| `rollback_initiated`                                                   | A rollback started                                 |
| `rollback_completed`                                                   | A rollback finished healthy                        |
| `rollback_failed`                                                      | A rollback did not become healthy                  |
| `health_check_started` / `health_check_passed` / `health_check_failed` | Health verification lifecycle                      |
| `state_saved`                                                          | State written to Redis                             |
| `manual_intervention_required`                                         | Automatic recovery is exhausted; a person must act |

## Inspecting Redis

```bash
kubectl -n orchestrator exec deploy/redis -- redis-cli keys 'deployment:*'
kubectl -n orchestrator exec deploy/redis -- redis-cli get 'last_successful:production:sample-app'
kubectl -n orchestrator exec deploy/redis -- redis-cli ttl 'deployment:<deployment-id>'
```

`last_successful:<namespace>:<app>` is the rollback target; it has no expiry. If a rollback keeps answering "No previous version", this key is probably missing.

## Best Practices

1. **Send a success webhook for every good deployment** so a rollback target always exists.
2. **Keep deployment IDs stable across retries** of the same logical deployment, or the failure count resets ([CI/CD Integration](cicd-integration.md#recommended-workflow-changes)).
3. **Monitor MANA itself.** Alert on `OrchestratorDown`, pod restarts, and `/health` reporting `degraded`.
4. **Watch the key metrics:** `deployments_total{status="failed"}`, `rollbacks_total`, `retries_total`. See [Monitoring](monitoring.md#prometheus-metrics).
5. **Alert on `manual_intervention_required`** in your log pipeline; it means automation gave up.
6. **Rehearse failures regularly** in staging with the [failure recipes](../configuration/sample-app.md#failure-recipes).
7. **Review the audit trail** periodically for recurring failure patterns.
8. **Protect the credentials**: webhooks are signed with `WEBHOOK_SECRET` and operator calls need `X-API-Key`; rotate them as described in [Security](../about/security.md).

## Troubleshooting Usage Issues

| Symptom                                              | Checks                                                                                                                                |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| MANA does not seem to react to a deployment          | Is the pipeline actually posting webhooks? `kubectl logs ... \| grep webhook`. Is `ORCHESTRATOR_URL` reachable from the runner?       |
| `GET /deployment/<id>` returns 404                   | The ID is wrong, the state expired (24 h), or the webhook was `in_progress`/`success` only (these do not store state)                 |
| Responses say `alert` with "missing workflow_run_id" | The failure webhook has no `metadata.workflow_run_id`; add it so a retry can be scheduled                                              |
| Retries are scheduled but nothing re-runs            | `GITHUB_TOKEN` or `GITHUB_REPO` is missing or invalid, or the run was still active; check logs for `Failed to trigger re-run`         |
| Rollback says "No previous version"                  | No success webhook was ever recorded and pods have no `version` label                                                                 |
| Rollback is unhealthy                                | Wrong `HEALTH_CHECK_PORT`/`PATH`, image pull problems, or a NetworkPolicy blocking the orchestrator; test the endpoint as shown above |

More in the [Troubleshooting Guide](troubleshooting.md).

## Next Steps

1. [API Reference](../reference/api.md)
2. [Configuration Guide](../configuration/overview.md)
3. [Monitoring](monitoring.md)
4. [CI/CD Integration](cicd-integration.md)
5. [Architecture Overview](../architecture/overview.md)
6. [Contributing](../contributing/contributing.md)
