# Sample Application Configuration

The sample application (`sample-app/`) is the deployment target that MANA protects. It is configured through environment variables read by `sample-app/src/config.py`. Several of those variables exist specifically so you can **break the app on purpose** and watch MANA respond. This page explains each setting and shows recipes for triggering each kind of failure.

For the flat variable list see [Environment Variables](environment-variables.md).

## How Configuration Is Loaded

`get_config()` builds a `Config` dataclass from the environment on first use and validates it in `__post_init__`. A violated rule raises `ValueError` and the app fails to start, which Kubernetes surfaces as `CrashLoopBackOff`.

## Settings

### Application

| Variable      | Default       | Validation                                                            | Description                                                                                                |
| ------------- | ------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `APP_VERSION` | `1.0.0`       | none                                                                  | Version string reported by `/health`, `/`, `/api/data`, `/api/config` and the `app_info` metric            |
| `ENVIRONMENT` | `development` | warns if not one of `development`, `staging`, `production`, `testing` | Environment name. `production` switches logging to JSON; `development` enables debug-level logs            |
| `PORT`        | `5000`        | `1024` to `65535`                                                     | Port used when running `python src/app.py`. The container's Gunicorn command binds `0.0.0.0:5000` directly |
| `DEBUG`       | `false`       | none                                                                  | Enables Flask debug mode (when run directly), debug log level, and extra detail in error responses         |

!!! tip "Keep `APP_VERSION` in step with the image tag"
The deploy workflow updates the image with `kubectl set image` but does not touch environment variables. The manifest pins `APP_VERSION=1.0.0`, so a running `v1a2b3c4-…` image keeps reporting `1.0.0`. If you want `/health` to show the real deployed version, set `APP_VERSION` to the tag (for example with `kubectl set env`) or inject it from the pod's image label.

### Failure simulation

| Variable       | Default | Validation     | Description                                                                        |
| -------------- | ------- | -------------- | ---------------------------------------------------------------------------------- |
| `FAILURE_RATE` | `0.0`   | `0.0` to `1.0` | Probability that `GET /` and `GET /api/data` return HTTP 500 (`SIMULATED_FAILURE`) |
| `STARTUP_TIME` | `0`     | `>= 0`         | Seconds after process start during which `GET /ready` returns HTTP 503 `NOT_READY` |

What these do and do not affect:

| Endpoint                                                      | `FAILURE_RATE` | `STARTUP_TIME`                  |
| ------------------------------------------------------------- | -------------- | ------------------------------- |
| `GET /health` (liveness, and the orchestrator's health check) | unaffected     | unaffected                      |
| `GET /ready` (readiness)                                      | unaffected     | 503 until the delay has elapsed |
| `GET /`, `GET /api/data`                                      | random 500s    | unaffected                      |
| `GET /api/stress`, `GET /api/config`, `GET /metrics`          | unaffected     | unaffected                      |

### Performance

| Variable    | Default | Validation | Description                        |
| ----------- | ------- | ---------- | ---------------------------------- |
| `WORKERS`   | `4`     | `>= 1`     | Intended Gunicorn worker count     |
| `TIMEOUT`   | `30`    | none       | Intended request timeout (seconds) |
| `KEEPALIVE` | `5`     | none       | Intended keep-alive (seconds)      |

!!! warning "Parsed but not consumed"
These three values are validated and stored in the config object, but the container starts with a fixed command (`gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 src.app:app`). To change worker count or timeout, edit the `CMD` in `sample-app/Dockerfile` or add `--workers $WORKERS` style arguments via a shell form.

### Monitoring

| Variable         | Default | Description                                                        |
| ---------------- | ------- | ------------------------------------------------------------------ |
| `ENABLE_METRICS` | `true`  | Reported in `GET /api/config`. Metrics are always exposed          |
| `METRICS_PORT`   | `5000`  | Not used. Metrics are served at `/metrics` on the application port |

## Observability Built In

- **Metrics**: `prometheus-flask-exporter` exposes request counts and latency histograms (for example `flask_http_request_total` and `flask_http_request_duration_seconds`) plus an `app_info{version=…}` gauge at `GET /metrics`.
- **Logs**: JSON to stdout when `ENVIRONMENT=production`, human-readable text otherwise. Each request gets an `X-Request-ID` (honouring an incoming header) that is returned in the response and included in every log line for that request.
- **Graceful shutdown**: SIGTERM and SIGINT run registered cleanup handlers and exit with status 0. Pair this with `terminationGracePeriodSeconds` in your Deployment if cleanup becomes non-trivial.

## Kubernetes Settings

`sample-app/k8s/deployment.yaml` sets only three variables (`APP_VERSION`, `ENVIRONMENT`, `PORT`) and configures:

| Setting                | Value                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------- |
| Replicas               | 3 (HPA range 3 to 10, CPU 70%, memory 80%)                                                                    |
| Rolling update         | `maxSurge: 1`, `maxUnavailable: 1`                                                                            |
| Resources              | requests 100m / 128Mi; limits 200m / 256Mi                                                                    |
| Liveness probe         | `GET /health`, initial delay 30 s, period 10 s, failure threshold 3                                           |
| Readiness probe        | `GET /ready`, initial delay 10 s, period 5 s, failure threshold 3                                             |
| Security context       | `runAsNonRoot`, UID 1000, no privilege escalation, all capabilities dropped (`readOnlyRootFilesystem: false`) |
| Pod Disruption Budget  | `minAvailable: 2`                                                                                             |
| Prometheus annotations | `prometheus.io/scrape: "true"`, port `5000`, path `/metrics`                                                  |

!!! note "Label and container-name contract with the orchestrator"
The orchestrator finds pods with the label selector `app=<deployment name>` and rolls back by editing the container whose **name equals the deployment name**. Keep `app: sample-app` on the pods and name the container `sample-app` (as the manifests do) or rollbacks and health checks will not find their targets. Pods also need the `version` label for the ReplicaSet-based previous-version fallback.

With Terraform, `failure_rate` is a root variable that is forced to `0.0` when `environment = "production"`; the staging copy of the app uses the value you provide. See the [Configuration Reference](../reference/configuration.md#terraform-variables).

## Failure Recipes

Use these in non-production environments to exercise MANA. Each one produces a different `failure_type` from the deploy workflow.

### 1. Pods never become ready (`deployment_failure`)

Make `/ready` fail for ten minutes:

```bash
kubectl set env deployment/sample-app STARTUP_TIME=600 -n staging
kubectl rollout status deployment/sample-app -n staging --timeout=60s   # times out
```

The new pods are never Ready, so `kubectl rollout status` fails in the deploy job and the pipeline reports `failure_type=deployment_failure`.

### 2. Intermittent application errors (smoke-test failures)

```bash
kubectl set env deployment/sample-app FAILURE_RATE=1.0 -n staging
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/        # 500
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/health  # 200
```

Pods stay Ready because `/health` and `/ready` are unaffected, but the production smoke tests (`/`, `/api/data`) and the `flask_http_request_total{status="500"}` metric will show the failure.

!!! warning
A smoke-test failure reaches the orchestrator as `failure_type=smoke_test_failure`, which the orchestrator currently rejects with HTTP 500. See [Data Flow: Known Behaviours](../architecture/data-flow.md#known-behaviours-and-limitations).

### 3. Bad image (`deployment_failure`)

Deploy a tag that does not exist or an image that exits immediately:

```bash
kubectl set image deployment/sample-app sample-app=ghcr.io/<owner>/mana/sample-app:does-not-exist -n staging
```

The pods go to `ImagePullBackOff`, the rollout never completes, and the deploy job fails.

### 4. Load and resource pressure

```bash
curl "http://localhost:8080/api/stress?duration=10&compute=true"
```

Run several in parallel to push CPU toward the HPA target and watch scaling in `kubectl get hpa -n production -w`.

## Running Locally

```bash
cd sample-app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ENVIRONMENT=development FAILURE_RATE=0.2 python -m flask --app src.app run --port 5000
# or
docker compose up sample-app      # docker-compose.yml sets FAILURE_RATE=0.1
```

Run the tests with `cd sample-app && pytest tests/ -v --cov=src`.
