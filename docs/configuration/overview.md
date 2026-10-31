# Configuration Guide

MANA is configured through **environment variables**. There is no configuration file, no ConfigMap-based loader and no Helm values file: the orchestrator and the sample app read the environment once at startup, validate it, and fail fast if a value is invalid.

This page is the starting point. It shows where to set values, summarises what you can tune, and gives ready-made profiles. Detailed pages:

- [Orchestrator Configuration](orchestrator.md): every orchestrator setting explained
- [Sample App Configuration](sample-app.md): settings and failure recipes for the demo app
- [Environment Variables](environment-variables.md): flat lookup of all variables, secrets and script inputs
- [Configuration Reference](../reference/configuration.md): defaults per deployment method, Terraform variables, ports, resources

## How Configuration Works

1. **Load.** `OrchestratorConfig.from_env()` reads variables with defaults. Types are converted with `int()`/`float()`, so a non-numeric value fails immediately.
2. **Validate.** `validate()` checks ranges (for example `MAX_RETRIES >= 1`, `MAX_BACKOFF > INITIAL_BACKOFF`).
3. **Start.** The orchestrator then connects to Redis and loads the Kubernetes config. Any failure aborts startup, and Kubernetes restarts the pod until it is fixed.
4. **Apply changes by restarting.** Values are not reloaded at runtime. After changing them, restart the pods. `GET /config` shows what the running process uses.

## Where to Set Values

| Deployment method                 | Where                                                                         | Apply with                                                                                              |
| --------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Kubernetes manifests              | `env:` list in `orchestrator/k8s/deployment.yaml`                             | `kubectl apply -f orchestrator/k8s/ && kubectl rollout restart deployment/orchestrator -n orchestrator` |
| Quick change on a running cluster | `kubectl set env`                                                             | `kubectl set env deployment/orchestrator MAX_RETRIES=2 -n orchestrator` (triggers a rollout)            |
| Terraform                         | `max_retries`, `replicas`, `resources` module variables | `terraform apply` (other variables are not exposed yet)                                                 |
| Docker Compose                    | `environment:` under `services.orchestrator` in `docker-compose.yml`          | `docker compose up -d`                                                                                  |
| Local process                     | `orchestrator/.env` (copy from `.env.example`) loaded into your shell         | restart `uvicorn`                                                                                       |

!!! note
Changes made with `kubectl set env` are overwritten the next time you `kubectl apply` the manifest or run Terraform. Update the source of truth too.

## What You Can Tune

### Orchestrator

| Area               | Variables                                                        | What it controls                                                                    |
| ------------------ | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Redis              | `REDIS_HOST`, `REDIS_PORT`                                       | Where state is stored                                                               |
| Retry and rollback | `MAX_RETRIES`                                                    | How many failures are retried before rollback                                       |
| Backoff            | `INITIAL_BACKOFF`, `BACKOFF_MULTIPLIER`, `MAX_BACKOFF`           | Delay before each retry                                                             |
| Health checks      | `HEALTH_CHECK_PORT`, `HEALTH_CHECK_PATH`, `HEALTH_CHECK_TIMEOUT` | How the application's health endpoint is called                                     |
| GitHub             | `GITHUB_TOKEN`, `GITHUB_REPO`                                    | Whether retries can trigger workflow re-runs                                        |
| Logging            | `LOG_LEVEL`, `ENABLE_AUDIT_LOGGING`                              | Applied at startup; every log line is one JSON object                               |
| Service            | `PORT`                                                           | Listen port when run with `python -m src.api`                                       |

### Fixed behaviours (not configurable today)

| Behaviour                                         | Value                                                       |
| ------------------------------------------------- | ----------------------------------------------------------- |
| Deployment state TTL in Redis                     | 24 hours                                                    |
| Consecutive application-health failures tolerated | 3                                                           |
| Overall health verification timeout               | 300 s (the manual `POST /health-check` accepts a `timeout`) |
| Redis connect and socket timeout                  | 5 s                                                         |
| Poll interval while waiting for readiness         | 10 s                                                        |

### Sample app

`APP_VERSION`, `ENVIRONMENT`, `PORT`, `DEBUG`, `FAILURE_RATE`, `STARTUP_TIME`, plus parsed-but-unused `WORKERS`, `TIMEOUT`, `KEEPALIVE`, `ENABLE_METRICS`, `METRICS_PORT`. See [Sample App Configuration](sample-app.md).

## Understanding the Key Settings

**Retries versus rollback.** Retries are decided first: while the failure count for a deployment ID is `<= MAX_RETRIES`, MANA schedules a re-run. Once it is higher, MANA rolls back if a previous version is known and the failed version is the one running. With the default (`3`) that means three retries and a rollback on the fourth failure. `MAX_RETRIES` is the only setting that decides when rollback starts; a leftover `ROLLBACK_THRESHOLD` is ignored and logged as deprecated. See [Data Flow](../architecture/data-flow.md#the-decision-rule).

**Backoff.** The delay before retry _n_ is `min(INITIAL_BACKOFF × BACKOFF_MULTIPLIER^(n-1), MAX_BACKOFF)`: 10, 20, 40 seconds by default. There is no jitter.

**Time to recovery.** Retries cost backoff time plus the time for each pipeline re-run. If your goal is a short MTTR, lower `MAX_RETRIES` and `INITIAL_BACKOFF`; if failures are usually flaky infrastructure, raise them.

**Health checks.** Verification needs both Kubernetes readiness and an HTTP 200 with `{"status": "healthy"}` from the configured path on the configured port. If your application listens elsewhere or answers differently, change `HEALTH_CHECK_PORT` / `HEALTH_CHECK_PATH` or adapt the app.

**GitHub integration.** `GITHUB_TOKEN` and `GITHUB_REPO` are required only for retries. Without them rollbacks still work.

## Profiles

Copy the block that fits and adjust. All variables are optional because every one has a default.

=== "Development"

    Fast feedback, short waits.

    ```bash
    MAX_RETRIES=2
    INITIAL_BACKOFF=2.0
    MAX_BACKOFF=30.0
    BACKOFF_MULTIPLIER=2.0
    HEALTH_CHECK_TIMEOUT=3
    ```

=== "Staging"

    Mirrors production behaviour, a little faster.

    ```bash
    MAX_RETRIES=3
    INITIAL_BACKOFF=5.0
    MAX_BACKOFF=60.0
    BACKOFF_MULTIPLIER=2.0
    HEALTH_CHECK_TIMEOUT=5
    ```

=== "Production"

    Patient but bounded.

    ```bash
    MAX_RETRIES=3
    INITIAL_BACKOFF=10.0
    MAX_BACKOFF=120.0
    BACKOFF_MULTIPLIER=2.0
    HEALTH_CHECK_TIMEOUT=5
    ```

=== "Fail fast"

    One retry, then roll back.

    ```bash
    MAX_RETRIES=1
    INITIAL_BACKOFF=5.0
    MAX_BACKOFF=30.0
    ```

Apply a profile to a running cluster:

```bash
kubectl set env deployment/orchestrator -n orchestrator \
  MAX_RETRIES=2 INITIAL_BACKOFF=5.0
kubectl rollout status deployment/orchestrator -n orchestrator
curl -s $MANA/config | jq
```

## Secrets

Only one setting is sensitive: `GITHUB_TOKEN`.

- Store it in a Kubernetes Secret and reference it with `secretKeyRef` (the shipped manifest expects Secret `github-token`, key `token`). Never put it in a ConfigMap, an image, or a committed file.
- Use a fine-grained token limited to one repository with _Actions: Read and write_.
- Rotate it regularly:

```bash
kubectl create secret generic github-token --from-literal=token="$NEW_TOKEN" \
  -n orchestrator --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/orchestrator -n orchestrator
```

For production-grade secret management (Sealed Secrets, External Secrets Operator, Vault) see [Security](../about/security.md).

## Validation Rules

| Setting                | Rule                                   |
| ---------------------- | -------------------------------------- |
| `MAX_RETRIES`          | integer, at least 1                    |
| `INITIAL_BACKOFF`      | number, greater than 0                 |
| `MAX_BACKOFF`          | number, greater than `INITIAL_BACKOFF` |
| `BACKOFF_MULTIPLIER`   | number, greater than 1.0               |
| `HEALTH_CHECK_TIMEOUT` | integer, greater than 0                |
| `HEALTH_CHECK_PORT`    | integer, 1 to 65535                    |

Error messages and fixes are listed in [Orchestrator Configuration](orchestrator.md#validation-errors). Validate before applying by running the orchestrator's config check locally:

```bash
cd orchestrator
MAX_RETRIES=0 python -c "from src.config import OrchestratorConfig as C; C.from_env().validate()"
# ValueError: max_retries must be >= 1
```

## Best Practices

1. **Keep manifests as the source of truth.** Avoid long-lived `kubectl set env` overrides.
2. **Change one thing at a time** and confirm with `GET /config`.
3. **Match the health settings to your app** before trusting rollbacks: a wrong path or port makes every verification fail.
4. **Make sure a rollback target exists.** Send a `success` webhook for each good deployment, and give pods a `version` label.
5. **Document non-default values** and why you chose them.
6. **Test in staging** with the failure recipes in [Sample App Configuration](sample-app.md#failure-recipes).
7. **Review the audit log** after tuning to see how decisions changed ([Monitoring](../usage/monitoring.md)).

## Troubleshooting Configuration

| Symptom                                       | Likely cause                                                                                                                          | Check                                                                                                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Pod `CrashLoopBackOff` right after a change   | Invalid value                                                                                                                         | `kubectl logs -n orchestrator -l app=orchestrator` shows the `ValueError`                                                                 |
| Change has no effect                          | Pods not restarted, or the manifest was re-applied over a `kubectl set env`                                                           | `curl $MANA/config`; `kubectl get deploy orchestrator -n orchestrator -o yaml`                                                            |
| `github_integration: false`                   | `GITHUB_TOKEN` or `GITHUB_REPO` missing (common with Terraform)                                                                       | `curl $MANA/health`; add the variables                                                                                                    |
| Every health check fails                      | Wrong `HEALTH_CHECK_PORT`/`PATH`, app returns something other than `{"status":"healthy"}`, or a NetworkPolicy blocks the orchestrator | `kubectl exec -n orchestrator deploy/orchestrator -- python -c "import requests;print(requests.get('http://<pod-ip>:5000/health').text)"` |
| `LOG_LEVEL=DEBUG` shows nothing extra         | The variable is read once at startup                                                                                                  | Restart the orchestrator after changing it                                                                                                |
| A variable from another document does nothing | It is not implemented                                                                                                                 | See below                                                                                                                                 |

### Settings you may see mentioned elsewhere

Earlier drafts of the documentation described options such as leader election, state TTL and cleanup intervals, jitter, namespace or label selectors for monitoring, and GitHub tuning variables. **None of these exist in the code today** (webhook and API authentication and the Redis password and TLS options do, and are documented in [Environment Variables](environment-variables.md)), and setting them has no effect. In particular, MANA does not watch namespaces by label: it acts on the deployments named in webhook payloads. The complete list of supported variables is in [Environment Variables](environment-variables.md).

## Next Steps

- [Orchestrator Configuration](orchestrator.md)
- [CI/CD Integration](../usage/cicd-integration.md)
- [Monitoring](../usage/monitoring.md)
- [Troubleshooting](../usage/troubleshooting.md)
