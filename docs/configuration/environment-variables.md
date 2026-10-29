# Environment Variables

A single lookup page for every environment variable and secret that MANA's code, scripts and workflows read. Narrative explanations are in [Orchestrator Configuration](orchestrator.md) and [Sample App Configuration](sample-app.md).

!!! info "Only what the code reads"
Each variable below was verified against the source (`os.getenv` calls, workflow `secrets.*` references and script defaults). Variables that appear in other documents but are not listed here are not read by the current code and have no effect. See [Not Implemented](#variables-that-have-no-effect).

Booleans accept `true` (case-insensitive) for on; any other value is treated as off.

## Orchestrator

Read in `orchestrator/src/config.py`, `orchestrator/src/orchestrator.py` and `orchestrator/src/api.py`.

| Variable               | Type   | Default     | Required       | Description                                                                 |
| ---------------------- | ------ | ----------- | -------------- | --------------------------------------------------------------------------- |
| `REDIS_HOST`           | string | `localhost` | yes in cluster | Redis hostname (`redis-service` in Kubernetes, `redis` in Compose)          |
| `REDIS_PORT`           | int    | `6379`      | no             | Redis port                                                                  |
| `MAX_RETRIES`          | int    | `3`         | no             | Failures answered with a retry before rolling back. Must be `>= 1`          |
| `INITIAL_BACKOFF`      | float  | `10.0`      | no             | Seconds before the first retry. Must be `> 0`                               |
| `MAX_BACKOFF`          | float  | `300.0`     | no             | Cap on any retry delay. Must be `> INITIAL_BACKOFF`                         |
| `BACKOFF_MULTIPLIER`   | float  | `2.0`       | no             | Delay growth factor. Must be `> 1.0`                                        |
| `HEALTH_CHECK_TIMEOUT` | int    | `5`         | no             | Per-request HTTP timeout (seconds) when probing a pod. Must be `> 0`        |
| `HEALTH_CHECK_PORT`    | int    | `5000`      | no             | Application port inside the pod. `1` to `65535`                             |
| `HEALTH_CHECK_PATH`    | string | `/health`   | no             | Application health path                                                     |
| `LOG_LEVEL`            | string | `INFO`      | no             | `CRITICAL`, `ERROR`, `WARNING`, `INFO` or `DEBUG` (any case); invalid values stop startup |
| `ENABLE_AUDIT_LOGGING` | bool   | `true`      | no             | `false` silences audit events; they are otherwise independent of `LOG_LEVEL` |
| `GITHUB_TOKEN`         | secret | unset       | for retries    | Token with permission to re-run workflows                                   |
| `GITHUB_REPO`          | string | unset       | for retries    | `owner/repo` that owns the workflow                                         |
| `PORT`                 | int    | `8000`      | no             | Listen port for `python -m src.api` only; the container uses a fixed `8000` |

Kubernetes also needs a Secret named `github-token` with key `token` if you use `orchestrator/k8s/deployment.yaml` as written.

## Sample Application

Read in `sample-app/src/config.py`.

| Variable         | Type   | Default       | Description                                                                                                      |
| ---------------- | ------ | ------------- | ---------------------------------------------------------------------------------------------------------------- |
| `APP_VERSION`    | string | `1.0.0`       | Version reported by the app and in the `app_info` metric                                                         |
| `ENVIRONMENT`    | string | `development` | `development`, `staging`, `production` or `testing` (other values log a warning). `production` enables JSON logs |
| `PORT`           | int    | `5000`        | Port for `python src/app.py`. Range `1024` to `65535`. Gunicorn in the image binds 5000 regardless               |
| `DEBUG`          | bool   | `false`       | Debug mode and verbose error responses                                                                           |
| `FAILURE_RATE`   | float  | `0.0`         | Probability (0.0 to 1.0) that `/` and `/api/data` return 500                                                     |
| `STARTUP_TIME`   | int    | `0`           | Seconds `/ready` returns 503 after start. `>= 0`                                                                 |
| `WORKERS`        | int    | `4`           | Parsed only; image uses 2 Gunicorn workers. `>= 1`                                                               |
| `TIMEOUT`        | int    | `30`          | Parsed only; image uses `--timeout 120`                                                                          |
| `KEEPALIVE`      | int    | `5`           | Parsed only                                                                                                      |
| `ENABLE_METRICS` | bool   | `true`        | Reported by `/api/config`; metrics are always served                                                             |
| `METRICS_PORT`   | int    | `5000`        | Parsed only; metrics share the app port                                                                          |

## GitHub Actions Secrets

Configure in **Settings, Secrets and variables, Actions**.

| Secret             | Used by                        | Purpose                                                                                                                                                  |
| ------------------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ORCHESTRATOR_URL` | all deploy and build workflows | Base URL of the orchestrator (for example `https://mana.example.com`). Must be reachable from GitHub-hosted runners. If empty, notifications are skipped |
| `KUBE_CONFIG`      | `deploy-environment.yml`       | Base64-encoded kubeconfig for the target cluster (`base64 -w0 kubeconfig`). The job fails if it is missing                                               |
| `GHCR_PAT`         | `build-and-test.yml`           | Personal access token with `write:packages` used to push to `ghcr.io`                                                                                    |
| `SNYK_TOKEN`       | `security-scan.yml`            | Snyk API token (the step is `continue-on-error`)                                                                                                         |

Workflow-level values set in YAML rather than as secrets: `REGISTRY=ghcr.io`, `IMAGE_NAME=<owner>/<repo>/sample-app`. `main-pipeline.yml` also defines `ENABLE_STAGING_DEPLOY` and `ENABLE_PRODUCTION_DEPLOY` (both `false`); the deploy jobs are currently gated by the manual `deploy_target` input rather than these flags.

## Deployment Script and Makefile

### `scripts/deploy.sh`

| Variable         | Default      | Description                                              |
| ---------------- | ------------ | -------------------------------------------------------- |
| `NAMESPACE`      | `production` | Target namespace for the sample app (also `--namespace`) |
| `APP_VERSION`    | `1.0.0`      | Image tag to build and deploy (also `--version`)         |
| `DEPLOY_TIMEOUT` | `600`        | Seconds to wait for each `kubectl wait`                  |

### `Makefile`

| Variable          | Default                       | Description                                             |
| ----------------- | ----------------------------- | ------------------------------------------------------- |
| `CLUSTER_NAME`    | `self-healing-cicd`           | Kind cluster name used by `load-images` and `clean-all` |
| `NAMESPACE`       | `production`                  | Namespace for app-related targets                       |
| `APP_VERSION`     | `<git short sha>-<unix time>` | Image tag                                               |
| `DOCKER_REGISTRY` | `localhost:5000`              | Declared; not currently used by any target              |
| `REPLICAS`        | none                          | Required by `make scale-app REPLICAS=N`                 |

Override on the command line: `make deploy-app NAMESPACE=staging APP_VERSION=1.2.3`.

## Terraform (`TF_VAR_*`)

Any root variable can be supplied as `TF_VAR_<name>`:

| Variable                   | Equivalent input                                        |
| -------------------------- | ------------------------------------------------------- |
| `TF_VAR_environment`       | `environment` (`local`, `dev`, `staging`, `production`) |
| `TF_VAR_app_version`       | `app_version`                                           |
| `TF_VAR_replicas`          | `replicas`                                              |
| `TF_VAR_enable_monitoring` | `enable_monitoring`                                     |
| `TF_VAR_enable_staging`    | `enable_staging`                                        |
| `TF_VAR_image_registry`    | `image_registry`                                        |
| `TF_VAR_failure_rate`      | `failure_rate`                                          |

Details and defaults: [Configuration Reference](../reference/configuration.md#terraform-variables).

## Docker Build Arguments

`APP_VERSION` and `VERSION` are passed as `--build-arg` by the CI workflows and `make build-app`, but neither Dockerfile declares a matching `ARG`, so the values are ignored (Docker prints a warning). Version the image through its tag and set `APP_VERSION` at runtime.

## Examples

=== "Local development (`orchestrator/.env`)"

    ```bash
    PORT=8000
    REDIS_HOST=localhost
    REDIS_PORT=6379
    MAX_RETRIES=3
    INITIAL_BACKOFF=10.0
    MAX_BACKOFF=300.0
    BACKOFF_MULTIPLIER=2.0
    HEALTH_CHECK_TIMEOUT=5
    HEALTH_CHECK_PORT=5000
    HEALTH_CHECK_PATH=/health
    LOG_LEVEL=INFO
    ENABLE_AUDIT_LOGGING=true
    GITHUB_TOKEN=ghp_xxx        # never commit a real token
    GITHUB_REPO=yourname/mana
    ```

=== "Kubernetes secret and env"

    ```bash
    kubectl create secret generic github-token --from-literal=token="$GITHUB_TOKEN" -n orchestrator
    kubectl set env deployment/orchestrator MAX_RETRIES=2 INITIAL_BACKOFF=5 -n orchestrator
    ```

=== "Sample app failure testing"

    ```bash
    kubectl set env deployment/sample-app FAILURE_RATE=0.5 STARTUP_TIME=0 -n staging
    kubectl set env deployment/sample-app FAILURE_RATE- -n staging     # remove the variable
    ```

## Variables That Have No Effect

The [Configuration Guide](overview.md) mentions options such as `ENABLE_LEADER_ELECTION`, `WEBHOOK_AUTH_ENABLED`, `WEBHOOK_SECRET`, `REDIS_PASSWORD`, `REDIS_SSL`, `STATE_TTL_SECONDS`, `MAX_APP_HEALTH_FAILURES`, `HEALTH_CHECK_INTERVAL`, `JITTER_ENABLED`, `AUDIT_LOG_RETENTION_DAYS`, `CLEANUP_OLD_STATES_INTERVAL`, `LOG_FORMAT` and the `GITHUB_*` tuning variables. These are not read by the current code, so setting them changes nothing. Treat them as a roadmap rather than a feature list. In particular:

- The state TTL is fixed at 24 hours.
- The tolerated number of consecutive application-health failures is fixed at 3.
- There is no webhook authentication; protect the endpoint at the network layer (see [Security](../about/security.md)).
