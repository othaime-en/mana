# Configuration Reference

A compact lookup for every tunable in the repository: environment defaults per deployment method, Terraform inputs, ports, Kubernetes resource settings, Redis keys, RBAC, and monitoring thresholds. For explanations and guidance see the [Configuration Guide](../configuration/overview.md), [Orchestrator Configuration](../configuration/orchestrator.md) and [Environment Variables](../configuration/environment-variables.md).

## Defaults by Deployment Method

"unset" means the method does not set the variable, so the code default applies.

| Variable               | Code default | `.env.example` | `docker-compose.yml` | `orchestrator/k8s/deployment.yaml` | Terraform module           |
| ---------------------- | ------------ | -------------- | -------------------- | ---------------------------------- | -------------------------- |
| `PORT`                 | `8000`       | `8000`         | `8000`               | `8000`                             | `8000`                     |
| `REDIS_HOST`           | `localhost`  | `localhost`    | `redis`              | `redis-service`                    | `redis-service`            |
| `REDIS_PORT`           | `6379`       | `6379`         | `6379`               | `6379`                             | `6379`                     |
| `MAX_RETRIES`          | `3`          | `3`            | `3`                  | `3`                                | `3` (`max_retries`)        |
| `INITIAL_BACKOFF`      | `10.0`       | `10.0`         | unset                | `10.0`                             | unset                      |
| `MAX_BACKOFF`          | `300.0`      | `300.0`        | unset                | `300.0`                            | unset                      |
| `BACKOFF_MULTIPLIER`   | `2.0`        | `2.0`          | unset                | `2.0`                              | unset                      |
| `HEALTH_CHECK_TIMEOUT` | `5`          | `5`            | unset                | `5`                                | unset                      |
| `HEALTH_CHECK_PORT`    | `5000`       | `5000`         | unset                | `5000`                             | unset                      |
| `HEALTH_CHECK_PATH`    | `/health`    | `/health`      | unset                | `/health`                          | unset                      |
| `LOG_LEVEL`            | `INFO`       | `INFO`         | `INFO`               | `INFO`                             | `INFO`                     |
| `ENABLE_AUDIT_LOGGING` | `true`       | `true`         | unset                | `true`                             | unset                      |
| `GITHUB_TOKEN`         | unset        | placeholder    | unset                | from Secret `github-token`         | **unset**                  |
| `GITHUB_REPO`          | unset        | placeholder    | unset                | `othaime-en/mana` (replace)        | **unset**                  |

Sample application defaults (code): `APP_VERSION=1.0.0`, `ENVIRONMENT=development`, `PORT=5000`, `FAILURE_RATE=0.0`, `STARTUP_TIME=0`, `DEBUG=false`. Compose sets `APP_VERSION=1.0.0-local`, `FAILURE_RATE=0.1`; the Kubernetes manifest sets `ENVIRONMENT=production`.

## Terraform Variables

### Root module (`terraform/variables.tf`)

| Variable            | Type   | Default      | Validation                                     | Description                                                                                           |
| ------------------- | ------ | ------------ | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `environment`       | string | `production` | one of `local`, `dev`, `staging`, `production` | Environment label applied to namespaces; `production` forces the sample app's `failure_rate` to `0.0` |
| `app_version`       | string | `1.0.0`      | none                                           | Image tag for the sample app                                                                          |
| `replicas`          | number | `3`          | 1 to 20                                        | Production sample-app replicas                                                                        |
| `staging_replicas`  | number | `2`          | none                                           | Staging sample-app replicas                                                                           |
| `enable_monitoring` | bool   | `true`       | none                                           | Install the monitoring stack                                                                          |
| `enable_staging`    | bool   | `true`       | none                                           | Deploy a second copy of the app to `staging`                                                          |
| `image_registry`    | string | `""`         | none                                           | Registry prefix (for example `ghcr.io/org`). Empty uses local images                                  |
| `failure_rate`      | number | `0.0`        | 0.0 to 1.0                                     | Failure simulation rate for non-production apps                                                       |

Root `required_version` is `>= 1.5.0`; providers are `hashicorp/kubernetes ~> 2.23` and `hashicorp/helm ~> 2.11`. Both providers read `~/.kube/config`.

### Orchestrator module (`terraform/modules/orchestrator/variables.tf`)

| Variable               | Default                                    | Description                                                                                                                     |
| ---------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `namespace`            | `orchestrator`                             | Target namespace                                                                                                                |
| `orchestrator_version` | `latest`                                   | Image tag                                                                                                                       |
| `replicas`             | `2`                                        | Orchestrator replicas                                                                                                           |
| `image_registry`       | `""`                                       | Registry prefix                                                                                                                 |
| `redis_enabled`        | `true`                                     | Deploy Redis (as `redis-service`)                                                                                               |
| `redis_password`       | `""` (sensitive)                           | Passes `--requirepass` to Redis. The orchestrator cannot authenticate to Redis yet, so leave empty unless you extend the client |
| `max_retries`          | `3`                                        | Written to env `MAX_RETRIES`                                                                                                    |
| `resources`            | 200m / 256Mi requests; 500m / 512Mi limits | Container resources                                                                                                             |

### Sample-app module (`terraform/modules/sample-app/variables.tf`)

`namespace` (`production`), `app_version` (`1.0.0`), `replicas` (`3`, 1 to 20), `image_registry` (`""`), `image_pull_policy` (`IfNotPresent`), `resources` (100m / 128Mi requests; 200m / 256Mi limits), `enable_hpa`, `failure_rate` and `ingress_host`. See the file for the full list.

### Outputs

`sample_app_service_url`, `orchestrator_url`, `grafana_url` and `prometheus_url` (the last two are `null` when monitoring is disabled).

### Backend

Local state in `terraform.tfstate` by default. `terraform/backend.tf` contains a commented S3 + DynamoDB block for shared state.

## Ports and Endpoints

| Service      | Container port | Service port                  | Typical local access                                                                       |
| ------------ | -------------- | ----------------------------- | ------------------------------------------------------------------------------------------ |
| Orchestrator | 8000           | 8000 (`orchestrator-service`) | `kubectl port-forward -n orchestrator svc/orchestrator-service 8000:8000`                  |
| Sample app   | 5000           | 80 (`sample-app-service`)     | `kubectl port-forward -n production svc/sample-app-service 8080:80`                        |
| Redis        | 6379           | 6379 (`redis-service`)        | cluster-internal                                                                           |
| Prometheus   | 9090           | 9090                          | `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090`   |
| Grafana      | 3000           | 80 (`prometheus-grafana`)     | `kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80`                        |
| Alertmanager | 9093           | 9093                          | `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093` |
| Loki         | 3100           | 3100                          | cluster-internal                                                                           |

With Docker Compose the same services publish `8000`, `5000`, `6379`, `9090`, `3000` and `3100` on localhost.

## Kubernetes Resource Settings

| Workload         | Replicas        | CPU request / limit | Memory request / limit | Probes                                                                                    |
| ---------------- | --------------- | ------------------- | ---------------------- | ----------------------------------------------------------------------------------------- |
| Orchestrator     | 2               | 100m / 500m         | 256Mi / 512Mi          | liveness and readiness `GET /health:8000`, 60 s delay, 20 s period                        |
| Redis (manifest) | 1               | 50m / 100m          | 64Mi / 128Mi           | none                                                                                      |
| Sample app       | 3 (HPA 3 to 10) | 100m / 200m         | 128Mi / 256Mi          | liveness `/health` (30 s delay, 10 s period); readiness `/ready` (10 s delay, 5 s period) |

All application pods run as non-root (UID 1000) with privilege escalation disabled and all capabilities dropped.

### Namespaces

| Namespace      | Purpose                                           |
| -------------- | ------------------------------------------------- |
| `production`   | Production sample app                             |
| `staging`      | Staging sample app                                |
| `canary`       | Reserved for canary deployments (no workload yet) |
| `orchestrator` | Orchestrator and Redis                            |
| `monitoring`   | Prometheus, Grafana, Loki, Alertmanager           |

Terraform labels each namespace with `name=<namespace>`, which the NetworkPolicies use for selectors. Namespaces created by other means need that label added manually.

### RBAC (`orchestrator-role`, cluster-wide)

| API group | Resources                    | Verbs                                                                                   |
| --------- | ---------------------------- | --------------------------------------------------------------------------------------- |
| `apps`    | `deployments`, `replicasets` | `get`, `list`, `watch`, `update`, `patch`                                               |
| core      | `pods`                       | `get`, `list`, `watch`                                                                  |
| core      | `events`                     | `create`, `patch`                                                                       |
| core      | `services`                   | `get`, `list`, `watch` (Terraform and `kubernetes/security/network-policies.yaml` only) |

### Quotas and limits (production namespace)

From `kubernetes/security/network-policies.yaml` and the Terraform `security` module: requests 10 CPU / 20Gi, limits 20 CPU / 40Gi, 50 pods, plus a `LimitRange` with per-container defaults of 200m CPU / 256Mi memory.

## Redis Data

| Key                                      | Value                  | TTL             | Written by         |
| ---------------------------------------- | ---------------------- | --------------- | ------------------ |
| `deployment:<deployment_id>`             | JSON `DeploymentState` | 86400 s (fixed) | every state change |
| `last_successful:<namespace>:<app_name>` | version string         | none            | `success` webhook  |

Redis runs with `--appendonly yes` in Compose and the Kubernetes manifest. The manifest mounts an `emptyDir`, so data does not survive pod rescheduling.

## Alert Thresholds

Defined in `monitoring/prometheus/alerts.yml`. Severity drives Alertmanager routing: `critical` goes to the critical receivers (PagerDuty and Slack), `warning` to Slack.

| Alert                                                                               | Condition                                                         | For        | Severity |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ---------- | -------- |
| `DeploymentReplicasMismatch`                                                        | spec replicas differ from available (production, staging, canary) | 5 m        | warning  |
| `HighRollbackRate`                                                                  | `rate(rollbacks_total[5m]) > 0.1`                                 | 2 m        | critical |
| `DeploymentFailureRate`                                                             | failed / total deployments over 10 m `> 20%`                      | 5 m        | warning  |
| `DeploymentSuccessRateBelowSLO`                                                     | success rate over 1 h `< 95%`                                     | 10 m       | critical |
| `PodCrashLooping`                                                                   | restart rate over 15 m `> 0.1`                                    | 5 m        | warning  |
| `SlowDeployment`                                                                    | p95 `deployment_duration_seconds` `> 600`                         | 10 m       | warning  |
| `OrchestratorDown`                                                                  | `up{job="orchestrator"} == 0`                                     | 2 m        | critical |
| `SlowRollbackDecision`                                                              | p95 `rollback_decision_duration_seconds > 1`                      | 5 m        | warning  |
| `HighWebhookFailureRate`                                                            | `webhook_errors_total / webhook_requests_total > 5%`              | 5 m        | warning  |
| `RedisConnectionFailures`                                                           | `rate(redis_connection_errors_total[5m]) > 0`                     | 2 m        | critical |
| `HighHealthCheckFailureRate`                                                        | failed health checks `> 10%`                                      | 5 m        | warning  |
| `HighErrorRate`                                                                     | 5xx ratio of `flask_http_request_total`                           | 5 m        | critical |
| `SlowResponseTime`                                                                  | p95 request latency `> 1 s`                                       | 10 m       | warning  |
| `HighMemoryUsage` / `HighCPUUsage`                                                  | container usage vs limit                                          | 10 m       | warning  |
| `ApplicationDown`                                                                   | `up{job="sample-app"} == 0`                                       | 2 m        | critical |
| `NodeNotReady`, `DiskSpaceLow`                                                      | node or disk problems                                             | 5 m / 10 m | critical |
| `HighNodeCPU` (`> 80%`), `HighNodeMemory` (`> 85%`), `TooManyPodsPerNode` (`> 100`) | node pressure                                                     | 10 m       | warning  |
| `PrometheusScrapeFailing`, `PrometheusStorageFull`, `GrafanaDown`, `LokiDown`       | monitoring health                                                 | 5 to 10 m  | warning  |

!!! warning "Alerts that depend on metrics the orchestrator does not emit yet"
`SlowRollbackDecision`, `HighWebhookFailureRate`, `RedisConnectionFailures`, `HighHealthCheckFailureRate` and `SlowDeployment` query `rollback_decision_duration_seconds`, `webhook_requests_total`/`webhook_errors_total`, `redis_connection_errors_total`, `health_checks_total` and `deployment_duration_seconds`. The orchestrator declares only some of these (`deployment_duration_seconds` is declared but never observed) and none of the others, so these alerts will not fire until the metrics are implemented. See [Monitoring](../usage/monitoring.md#prometheus-metrics).

## Monitoring Settings

| Setting                                 | Value                                                                          | Where                                                 |
| --------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------- |
| Prometheus scrape / evaluation interval | 15 s                                                                           | `monitoring/prometheus/prometheus.yml`                |
| Prometheus retention                    | 7 days, 10 GB, 10Gi volume                                                     | Terraform `monitoring` module and Helm values         |
| Loki retention                          | 720 h (30 days)                                                                | `monitoring/loki/loki-config.yml`                     |
| Alertmanager grouping                   | `alertname`, `namespace`, `severity`                                           | `monitoring/alertmanager/alertmanager.yml`            |
| Alertmanager repeat interval            | 12 h default; 4 h critical; 2 h for orchestrator/application critical          | `monitoring/alertmanager/alertmanager.yml`            |
| Grafana admin                           | user `admin`, password `admin123`                                              | Terraform and Helm values. **Change before exposing** |
| Dashboards                              | Self-Healing Overview, Deployment Metrics, Application Metrics, Infrastructure | `monitoring/grafana/dashboards/`                      |

Alertmanager reads Slack, SMTP and PagerDuty secrets from files under `/etc/alertmanager/` (`slack_webhook_url`, `smtp_password`); mount them from Kubernetes Secrets. Replace the placeholder sender and SMTP host in `alertmanager.yml` before use.
