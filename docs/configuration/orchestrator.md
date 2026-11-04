# Orchestrator Configuration

The orchestrator is configured entirely through environment variables, read once at startup by `orchestrator/src/config.py`. This page explains what each setting does, how the settings interact, and how to set them in each deployment method. For a flat list of every variable see [Environment Variables](environment-variables.md); for the cross-component defaults table see the [Configuration Reference](../reference/configuration.md).

!!! info "Source of truth"
This page documents the variables that the code actually reads. The broader [Configuration Guide](overview.md) describes some additional options (leader election, and so on) that are not implemented in the current code. If a variable is not listed here or in [Environment Variables](environment-variables.md), the orchestrator ignores it.

## How Configuration Is Loaded

1. `get_config()` builds an `OrchestratorConfig` with `OrchestratorConfig.from_env()` the first time it is called and caches it.
2. `validate()` checks the values and raises `ValueError` on the first violation.
3. `SelfHealingOrchestrator.__init__` validates again, connects to Redis (`PING`) and loads the Kubernetes config. An invalid value raises `ValueError`; an unreachable Redis or an unloadable Kubernetes config raises `RuntimeError`. In every case the process exits, so Kubernetes restarts the pod until the problem is fixed.

Changing a variable requires restarting the orchestrator pods. `GET /config` shows the values currently in effect (see the [API Reference](../reference/api.md#get-config)).

## Settings

### Redis

| Variable     | Default     | Description                                                      |
| ------------ | ----------- | ---------------------------------------------------------------- |
| `REDIS_HOST` | `localhost` | Hostname of the Redis server. In-cluster this is `redis-service` |
| `REDIS_PORT` | `6379`      | Redis port                                                       |
| `REDIS_PASSWORD` | empty   | Password sent to Redis. The manifests, Terraform and Compose start Redis with `--requirepass` from the `redis-password` secret |
| `REDIS_TLS` | `false`      | Use TLS and require a valid server certificate                   |
| `REDIS_TLS_CA_FILE` | empty | CA bundle for that certificate; needs `REDIS_TLS=true` and a file that exists |

The client uses a 5-second connect and socket timeout. The in-cluster Redis serves plain TCP behind a NetworkPolicy that admits only orchestrator pods; set `REDIS_TLS` when you point the orchestrator at a managed Redis that terminates TLS (see [Security](../about/security.md)).

### Authentication

| Variable         | Default | Description                                                                                          |
| ---------------- | ------- | ---------------------------------------------------------------------------------------------------- |
| `WEBHOOK_SECRET` | unset   | HMAC key for webhooks. At least 32 characters; a comma-separated list accepts several keys for rotation |
| `API_KEY`        | unset   | Key for the operator endpoints (`X-API-Key`). Same rules                                              |
| `AUTH_DISABLED`  | `false` | `true` disables every check and logs at CRITICAL. Local development only                              |

Without the first two (and without `AUTH_DISABLED=true`) the process exits at startup. Details of what each endpoint requires are in the [API Reference](../reference/api.md).

### Retry and rollback policy

| Variable             | Default | Constraint          | Description                                                          |
| -------------------- | ------- | ------------------- | -------------------------------------------------------------------- |
| `MAX_RETRIES`        | `3`     | `>= 1`              | Ceiling on the retries of every rule in the [recovery policy](recovery-policy.md) |
| `INITIAL_BACKOFF`    | `10.0`  | `> 0`               | Seconds to wait before the first retry                               |
| `BACKOFF_MULTIPLIER` | `2.0`   | `> 1.0`             | Growth factor between retries                                        |
| `MAX_BACKOFF`        | `300.0` | `> INITIAL_BACKOFF` | Upper bound on any single delay, in seconds                          |

The delay before retry _n_ is:

```
delay(n) = min(INITIAL_BACKOFF × BACKOFF_MULTIPLIER^(n-1), MAX_BACKOFF)
```

| Retry | Defaults (10 / 2 / 300) | Aggressive (2 / 2 / 30) | Conservative (30 / 3 / 600) |
| ----- | ----------------------- | ----------------------- | --------------------------- |
| 1     | 10 s                    | 2 s                     | 30 s                        |
| 2     | 20 s                    | 4 s                     | 90 s                        |
| 3     | 40 s                    | 8 s                     | 270 s                       |
| 4     | 80 s                    | 16 s                    | 600 s (capped)              |

**Choosing values.** Which failures are retried, how often, and when MANA rolls back at once is set by the [recovery policy](recovery-policy.md). `MAX_RETRIES` only caps the retries of every rule, so `MAX_RETRIES=1` allows at most one retry whatever the policy file says. `ROLLBACK_THRESHOLD` was removed; if it is still set, the orchestrator logs a deprecation warning and ignores it.

With the built-in policy a health check failure is rolled back on the first failure, with no backoff and no re-run. Backoff only applies to the failures that are retried (a build failure, a deployment failure, a timeout). Rollbacks are verified every `HEALTH_POLL_SECONDS` (default `5`) for at most the policy's `rollback.verify_timeout_seconds`.

### Job scheduler

Retries, rollbacks and alerts are not run inside the webhook request. The webhook decides, saves the deployment state and queues a **job** in Redis; every replica runs a scheduler that claims due jobs.

- A claimed job is **leased** for `JOB_LEASE_SECONDS` and the lease is renewed while it runs. If the replica dies, the lease expires and another replica runs the job, so a pod restart during a backoff or a rollback loses nothing. Expect recovery of a killed worker within about one lease (60 s by default).
- A job that fails is retried with the same exponential backoff as deployments, up to `JOB_MAX_ATTEMPTS`; then the deployment is marked `failed`, an audit event `manual_intervention_required` is written and an alert is sent.
- GitHub answers 403 or 409 while a run is still in progress and 429 or 5xx under load; those are retried. Other 4xx answers (bad token, unknown run) and a missing `GITHUB_TOKEN` fail the job at once.
- Rollbacks take a lock per `(namespace, app)`, so two replicas never roll the same application back at the same time. A job that finds the lock taken waits and tries again without using an attempt.

| Variable                 | Default | Constraint | Description                                                      |
| ------------------------ | ------- | ---------- | ---------------------------------------------------------------- |
| `JOB_LEASE_SECONDS`      | `60`    | `>= 3`     | Lease on a running job and on the per-app lock                   |
| `SCHEDULER_POLL_SECONDS` | `1`     | `> 0`      | Polling interval of each replica                                 |
| `SCHEDULER_CONCURRENCY`  | `4`     | `>= 1`     | Jobs one replica runs at once                                    |
| `JOB_MAX_ATTEMPTS`       | `5`     | `>= 1`     | Attempts before a job is abandoned                               |
| `SHUTDOWN_GRACE_SECONDS` | `30`    | `>= 0`     | Wait for running jobs on shutdown; jobs left over are requeued   |

On shutdown a replica first fails `/readyz` (so traffic moves away), stops claiming jobs and waits up to `SHUTDOWN_GRACE_SECONDS`. Use a `terminationGracePeriodSeconds` larger than that.

### Alerts

| Variable              | Default | Description                                                                                  |
| --------------------- | ------- | -------------------------------------------------------------------------------------------- |
| `SLACK_WEBHOOK_URL`   | unset   | Slack incoming webhook (`https://` only). Kept in a Secret named `slack-webhook`, key `url`  |
| `ALERT_GITHUB_STATUS` | `true`  | Set a `mana/recovery` commit status; active when `GITHUB_TOKEN` and `GITHUB_REPO` are set    |

```bash
kubectl -n orchestrator create secret generic slack-webhook --from-literal=url='https://hooks.slack.com/services/...'
```

Alerts are sent when a rollback completes (warning), when a rollback fails or a job is abandoned, and whenever automatic recovery cannot continue (critical): a missing `workflow_run_id`, no previous version, a build or test failure after retries, or a live version that cannot be read. Retries and skipped rollbacks are not alerted. Each alert is a queued job per channel, so a failing channel is retried without delaying recovery or the other channel and can never change a deployment's outcome. The commit status needs the `commit` the pipeline sends in the webhook metadata, which `scripts/notify-orchestrator.sh` adds from `GITHUB_SHA`.

### Health checks

| Variable               | Default   | Constraint     | Description                                                          |
| ---------------------- | --------- | -------------- | -------------------------------------------------------------------- |
| `HEALTH_CHECK_TIMEOUT` | `5`       | `> 0`          | Seconds to wait for **each** HTTP request to a pod's health endpoint |
| `HEALTH_CHECK_PORT`    | `5000`    | `1` to `65535` | Port the application listens on inside its pod                       |
| `HEALTH_CHECK_PATH`    | `/health` | none           | Path of the application's health endpoint                            |

The application health check is only the second half of verification. First, Kubernetes must report `ready_replicas == replicas`. The orchestrator then calls `http://<pod-ip>:<HEALTH_CHECK_PORT><HEALTH_CHECK_PATH>` on every running pod and expects HTTP 200 with a JSON body containing `"status": "healthy"`.

Two related values are **not** environment variables:

- The overall verification timeout is 300 s by default (`check_deployment_health(timeout=300)`); the manual `POST /health-check` endpoint accepts a `timeout` field.
- The number of consecutive application-health failures tolerated before giving up is fixed at 3 (`max_app_health_failures`).

If your application returns a different health payload, either make it return `{"status": "healthy"}` or adapt `check_application_health()`.

### GitHub integration

| Variable       | Default | Description                                |
| -------------- | ------- | ------------------------------------------ |
| `GITHUB_TOKEN` | unset   | Token used to call the workflow re-run API |
| `GITHUB_REPO`  | unset   | Repository in `owner/repo` form            |

Both are read directly by the orchestrator (not through `OrchestratorConfig`). If either is missing the orchestrator starts, logs a warning, and any attempted retry fails with an error log. `GET /` and `GET /health` report `github_integration: false` in that case.

The token needs permission to re-run workflows: **Actions: Read and write** on a fine-grained token scoped to the repository, or the `repo` (and `workflow`) scopes on a classic token. Store it in a Kubernetes Secret, never in a ConfigMap or image:

```bash
kubectl create secret generic github-token \
  --from-literal=token="$GITHUB_TOKEN" \
  -n orchestrator
```

`orchestrator/k8s/deployment.yaml` already reads it as `secretKeyRef: {name: github-token, key: token}`.

### Logging and audit

| Variable               | Default | Description                       |
| ---------------------- | ------- | --------------------------------- |
| `LOG_LEVEL`            | `INFO`  | Application and uvicorn log level |
| `ENABLE_AUDIT_LOGGING` | `true`  | `false` silences audit events     |

!!! warning "Parsed but not yet enforced"
Both values are loaded and validated into the config object, but the logging setup in `orchestrator.py` and `api.py` uses a fixed `INFO` level, and the audit logger is always on. Changing these variables currently has no effect on output.

### Service

| Variable | Default | Description                                                                                                                                          |
| -------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PORT`   | `8000`  | Listen port when starting with `python -m src.api`. The container `CMD` uses `uvicorn --port 8000` directly, so `PORT` does not affect the container |

## Setting Values by Deployment Method

=== "Local process"

    ```bash
    cd orchestrator
    cp .env.example .env          # edit values, never commit .env
    set -a; source .env; set +a
    uvicorn src.api:app --reload --port 8000
    ```

=== "Docker Compose"

    Set values under `services.orchestrator.environment` in `docker-compose.yml`. The shipped file sets `REDIS_HOST=redis`, `MAX_RETRIES=3` and `LOG_LEVEL=INFO`. Add `GITHUB_TOKEN` and `GITHUB_REPO` there (or via an `env_file`) to enable retries.

=== "Kubernetes manifests"

    Edit the `env:` list in `orchestrator/k8s/deployment.yaml`, then:

    ```bash
    kubectl apply -f orchestrator/k8s/ -n orchestrator
    kubectl rollout restart deployment/orchestrator -n orchestrator
    ```

    Remember to change `GITHUB_REPO` (marked `# REPLACE THIS`) to your repository.

=== "Terraform"

    The `orchestrator` module exposes `max_retries` (plus `replicas`, `resources` and Redis options). The Deployment it creates sets only `PORT`, `REDIS_HOST`, `REDIS_PORT`, `MAX_RETRIES` and `LOG_LEVEL`. It does **not** set `GITHUB_TOKEN` or `GITHUB_REPO`, so a Terraform-deployed orchestrator has no GitHub integration (retries cannot trigger re-runs) until you add them. See [Configuration Reference](../reference/configuration.md#terraform-variables).

## Presets

Copy-paste starting points. Adjust to your risk tolerance.

```bash
# Development: fast feedback
MAX_RETRIES=2
INITIAL_BACKOFF=2.0
MAX_BACKOFF=30.0
BACKOFF_MULTIPLIER=2.0
HEALTH_CHECK_TIMEOUT=3
```

```bash
# Production: patient, but bounded
MAX_RETRIES=3
INITIAL_BACKOFF=10.0
MAX_BACKOFF=120.0
BACKOFF_MULTIPLIER=2.0
HEALTH_CHECK_TIMEOUT=5
```

## Validation Errors

`validate()` raises `ValueError` with one of these messages. The pod will crash-loop until the value is corrected; check `kubectl logs -n orchestrator -l app=orchestrator`.

| Message                                     | Cause                                                    |
| ------------------------------------------- | -------------------------------------------------------- |
| `max_retries must be >= 1`                  | `MAX_RETRIES` is 0 or negative                           |
| `initial_backoff must be > 0`               | `INITIAL_BACKOFF` is 0 or negative                       |
| `max_backoff must be > initial_backoff`     | `MAX_BACKOFF` is less than or equal to `INITIAL_BACKOFF` |
| `health_poll_seconds must be > 0`           | `HEALTH_POLL_SECONDS` is 0 or negative                   |
| `POLICY_FILE not found: ...`                | `POLICY_FILE` names a file that does not exist           |
| `WATCHER_NAMESPACES ...`                    | The watcher is on and a namespace name is empty or invalid |
| `job_lease_seconds must be >= 3`            | `JOB_LEASE_SECONDS` is below 3                           |
| `scheduler_poll_seconds must be > 0`        | `SCHEDULER_POLL_SECONDS` is 0 or negative                |
| `scheduler_concurrency must be >= 1`        | `SCHEDULER_CONCURRENCY` is below 1                       |
| `job_max_attempts must be >= 1`             | `JOB_MAX_ATTEMPTS` is below 1                            |
| `shutdown_grace_seconds must be >= 0`       | `SHUTDOWN_GRACE_SECONDS` is negative                     |
| `SLACK_WEBHOOK_URL must be an https:// URL` | The Slack URL does not start with `https://`             |
| `backoff_multiplier must be > 1.0`          | `BACKOFF_MULTIPLIER` is 1.0 or less                      |
| `health_check_timeout must be > 0`          | `HEALTH_CHECK_TIMEOUT` is 0 or negative                  |
| `health_check_port must be between 1-65535` | `HEALTH_CHECK_PORT` out of range                         |

Non-numeric values for numeric variables fail earlier with Python's own `ValueError: invalid literal for int()`.

## Resources and Permissions

| Setting           | Value                                                                                                                         | Where                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Replicas          | 2                                                                                                                             | `orchestrator/k8s/deployment.yaml` (the Terraform module's `replicas` variable also defaults to 2) |
| Requests / limits | 100m CPU, 256Mi / 500m CPU, 512Mi                                                                                             | `orchestrator/k8s/deployment.yaml`                                                                 |
| Probes            | startup and liveness `GET /livez`, readiness `GET /readyz`, all on 8000                                                       | `orchestrator/k8s/deployment.yaml`                                                                 |
| Availability      | `PodDisruptionBudget` `maxUnavailable: 1`, topology spread across nodes, rolling update with `maxUnavailable: 0`, `terminationGracePeriodSeconds: 45` | `orchestrator/k8s/deployment.yaml` |
| Security context  | `runAsNonRoot`, UID 1000, no privilege escalation, all capabilities dropped, `seccomp` `RuntimeDefault`, read-only root filesystem with an `emptyDir` at `/tmp` | `orchestrator/k8s/deployment.yaml` |
| RBAC              | per managed namespace (`production`, `staging`, `canary`): `get`, `patch` on Deployments; `list` on ReplicaSets and Pods. No cluster-wide permissions | `orchestrator/k8s/rbac.yaml` |

!!! note "Running two replicas"
Both replicas serve webhooks and share state through Redis. There is no leader election, so two replicas can both act on the same deployment ID if the CI system delivers a webhook twice. Use a single replica if you need strict single-actor behaviour.
