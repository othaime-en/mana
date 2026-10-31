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
| `MAX_RETRIES`        | `3`     | `>= 1`              | Number of failures answered with a retry; the next failure rolls back |
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

**Choosing values.** The decision rule is retry while `failure_count <= MAX_RETRIES`, otherwise roll back if a previous version exists and the failed version is the one running (see [Data Flow](../architecture/data-flow.md#the-decision-rule)):

- `MAX_RETRIES=3` (default): three retries, rollback on the fourth failure.
- `MAX_RETRIES=1`: one retry, rollback on the second failure. Good for fast-moving staging environments.
- `MAX_RETRIES=0` is rejected by validation, so at least one retry is always attempted before a rollback. There is currently no "roll back immediately" mode.
- `ROLLBACK_THRESHOLD` was removed. If it is still set, the orchestrator logs a deprecation warning and ignores it.

The worst-case time spent retrying before a rollback is the sum of the backoff delays plus the time each re-run takes. With the defaults that is 70 s of backoff alone. If your target MTTR is under a minute, lower `INITIAL_BACKOFF` and `MAX_RETRIES`.

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
| `backoff_multiplier must be > 1.0`          | `BACKOFF_MULTIPLIER` is 1.0 or less                      |
| `health_check_timeout must be > 0`          | `HEALTH_CHECK_TIMEOUT` is 0 or negative                  |
| `health_check_port must be between 1-65535` | `HEALTH_CHECK_PORT` out of range                         |

Non-numeric values for numeric variables fail earlier with Python's own `ValueError: invalid literal for int()`.

## Resources and Permissions

| Setting           | Value                                                                                                                         | Where                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Replicas          | 2                                                                                                                             | `orchestrator/k8s/deployment.yaml` (the Terraform module's `replicas` variable also defaults to 2) |
| Requests / limits | 100m CPU, 256Mi / 500m CPU, 512Mi                                                                                             | `orchestrator/k8s/deployment.yaml`                                                                 |
| Probes            | `GET /health` on 8000, 60 s initial delay, 20 s period                                                                        | `orchestrator/k8s/deployment.yaml`                                                                 |
| Security context  | `runAsNonRoot`, UID 1000, no privilege escalation, all capabilities dropped, `seccomp` `RuntimeDefault`, read-only root filesystem with an `emptyDir` at `/tmp` | `orchestrator/k8s/deployment.yaml` |
| RBAC              | per managed namespace (`production`, `staging`, `canary`): `get`, `patch` on Deployments; `list` on ReplicaSets and Pods. No cluster-wide permissions | `orchestrator/k8s/rbac.yaml` |

!!! note "Running two replicas"
Both replicas serve webhooks and share state through Redis. There is no leader election, so two replicas can both act on the same deployment ID if the CI system delivers a webhook twice. Use a single replica if you need strict single-actor behaviour.
