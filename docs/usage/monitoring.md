# Monitoring and Observability

MANA ships a complete observability setup (Prometheus, Grafana, Loki, Alertmanager) and instruments both the orchestrator and the sample app. This guide explains what is measured, how to install and access the stack, which dashboards and alerts exist, and where the current gaps are.

## What Is Observable

| Signal                            | Source                                        | Where it goes                      |
| --------------------------------- | --------------------------------------------- | ---------------------------------- |
| Orchestrator metrics              | `GET /metrics/` on port 8000                  | Prometheus                         |
| Sample app metrics                | `GET /metrics` on port 5000                   | Prometheus                         |
| Kubernetes metrics                | kube-state-metrics, cAdvisor, node exporter   | Prometheus                         |
| Audit events and application logs | stdout of each pod                            | Promtail to Loki                   |
| Health                            | `GET /health` on the orchestrator and the app | Kubernetes probes, Prometheus `up` |

Configuration files are in `monitoring/` (see [Components](../architecture/components.md#monitoring-stack)).

## Prometheus Metrics

### Orchestrator

| Metric                          | Type      | Labels                       | Meaning                                                           | Populated                |
| ------------------------------- | --------- | ---------------------------- | ----------------------------------------------------------------- | ------------------------ |
| `deployments_total`             | counter   | `namespace`, `status`        | Webhooks received, by status (`in_progress`, `success`, `failed`) | yes                      |
| `retries_total`                 | counter   | `namespace`, `retry_attempt` | Retries scheduled                                                 | yes                      |
| `rollbacks_total`               | counter   | `namespace`, `reason`        | Successful rollbacks (`reason` is the failure type, or `manual`)  | yes                      |
| `health_check_duration_seconds` | histogram | `namespace`, `deployment`    | Duration of `POST /health-check` calls                            | yes                      |
| `deployment_duration_seconds`   | histogram | `namespace`                  | Intended deployment duration                                      | declared, never observed |
| `active_deployments`            | gauge     | `namespace`                  | Intended count of in-flight deployments                           | declared, never set      |

Standard `python_*` and `process_*` metrics are also exported. Use `curl -s $MANA/metrics/` to see the live output.

### Sample app

Provided by `prometheus-flask-exporter` at `GET /metrics`:

| Metric                                    | Meaning              |
| ----------------------------------------- | -------------------- |
| `flask_http_request_total{method,status}` | Request count        |
| `flask_http_request_duration_seconds`     | Latency histogram    |
| `flask_http_request_exceptions_total`     | Unhandled exceptions |
| `app_info{version}`                       | Application version  |

### Useful queries

```promql
# Deployment events by status over the last hour
sum(increase(deployments_total[1h])) by (status)

# Failure ratio per namespace (5 min)
sum(rate(deployments_total{status="failed"}[5m])) by (namespace)
  / sum(rate(deployments_total[5m])) by (namespace)

# Rollbacks in the last 24 h, by reason
sum(increase(rollbacks_total[24h])) by (reason)

# Retries by attempt number
sum(increase(retries_total[1h])) by (retry_attempt)

# Sample app 5xx error ratio
sum(rate(flask_http_request_total{status=~"5.."}[5m]))
  / sum(rate(flask_http_request_total[5m]))

# Sample app p95 latency
histogram_quantile(0.95, sum(rate(flask_http_request_duration_seconds_bucket[5m])) by (le))

# Pod restarts
sum(rate(kube_pod_container_status_restarts_total{namespace="production"}[15m])) by (pod)
```

### Recording rules

`monitoring/prometheus/recording-rules.yml` pre-computes success, failure, rollback and retry rates (`deployment:success_rate:5m`, `deployment:rollback_rate:1h`, and so on), HTTP rates and latency percentiles (`http:*`), node and pod resource usage, SLO and error-budget series (`slo:*`, `mttr:rollback_duration:avg:24h`), and some orchestrator series (`orchestrator:webhook_error_rate:5m`, `orchestrator:rollback_decision_seconds:p95:5m`). Rules that depend on metrics the orchestrator does not emit (webhook, rollback decision and deployment duration series) produce no data yet.

### How Prometheus finds the targets

| Deployment                            | Mechanism                                                                                                                                             |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Terraform (`kube-prometheus-stack`)   | ServiceMonitors created by the `monitoring` module, plus pod annotations on the orchestrator (`prometheus.io/scrape`, `port: 8000`, `path: /metrics`) |
| Sample app manifests                  | Pod annotations `prometheus.io/scrape: "true"`, `port: "5000"`, `path: "/metrics"`                                                                    |
| Standalone `prometheus.yml` (Compose) | Dedicated `sample-app` and `orchestrator` jobs plus annotation-based pod discovery                                                                    |

!!! warning "Target configuration to double-check" - The manual orchestrator manifest (`orchestrator/k8s/deployment.yaml`) has no `prometheus.io/*` annotations, so annotation-based discovery will not find it; add them to the pod template or use the Terraform module. - In `prometheus.yml` the `sample-app` and `orchestrator` jobs set `__address__` to the literal text `__meta_kubernetes_pod_ip:<port>` instead of building it from the label with `source_labels` and a `${1}` replacement, so those two jobs likely will not resolve a valid address. Verify on the Prometheus **Targets** page and fix the relabeling if they show errors. - The Compose Prometheus loads `alerts.yml` but does not mount `recording-rules.yml`, and expects an Alertmanager at `alertmanager:9093` that Compose does not run.

## Structured Logging

### Audit events

Every significant orchestrator action produces an audit event (`deployment_received`, `failure_detected`, `retry_initiated`, `rollback_initiated`, `rollback_completed`, `rollback_failed`, `rollback_skipped`, `health_check_*`, `state_saved`, `manual_intervention_required`). Each carries a UTC timestamp, deployment ID, namespace, version and a `success` flag, plus action-specific details.

```bash
kubectl -n orchestrator logs -l app=orchestrator | grep "AUDIT:"
```

The orchestrator's default log format prints `AUDIT: <action>` and attaches the structured payload to the record as `audit_event`, which the plain text formatter does not print. To ship the complete JSON to Loki, configure a JSON formatter (for example `python-json-logger`, already used by the sample app) that includes extra fields. Until then, correlate by `deployment_id` in the surrounding log lines.

### Sample app logs

With `ENVIRONMENT=production` the sample app logs JSON with a per-request correlation ID (`X-Request-ID`); otherwise plain text.

### Querying logs in Loki

Promtail labels logs with `namespace`, `pod`, `container`, `app` and `version` (and `level`/`logger` when the line is JSON). In Grafana, **Explore**, choose the Loki data source:

```logql
{namespace="orchestrator", app="orchestrator"}
{namespace="orchestrator"} |= "AUDIT:"
{namespace="orchestrator"} |= "manual_intervention_required"
{namespace="production", app="sample-app"} | json | level="ERROR"
sum(rate({namespace="production", app="sample-app"} | json | level="ERROR" [5m]))
```

Loki retains logs for 30 days (`720h`) in the shipped config.

## Health Endpoints

| Service      | Endpoint      | Meaning                                                                   |
| ------------ | ------------- | ------------------------------------------------------------------------- |
| Orchestrator | `GET /health` | `healthy` or `degraded`; reports Redis, Kubernetes and GitHub integration |
| Sample app   | `GET /health` | Liveness                                                                  |
| Sample app   | `GET /ready`  | Readiness (503 until `STARTUP_TIME` passes)                               |

The orchestrator also exposes `GET /config` to confirm its live settings.

## Dashboards

Four Grafana dashboards live in `monitoring/grafana/dashboards/`:

| Dashboard                 | Shows                                                                                                                                |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **Self-Healing Overview** | Success rate, rollback and retry activity, failed deployments, MTTR and error budget, orchestrator health, pod restarts, recent logs |
| **Deployment Metrics**    | Per-namespace deployment and rollback statistics, durations, SLO and error budget                                                    |
| **Application Metrics**   | Request rate, error rate, latency percentiles, resource usage of the sample app                                                      |
| **Infrastructure**        | Node CPU and memory, cluster capacity, quotas                                                                                        |

Several panels use the recording rules above. Panels built on `active_deployments`, deployment duration and orchestrator rollback-decision series are empty until those metrics are emitted.

### Access

```bash
make port-forward-grafana          # http://localhost:3000
make port-forward-prometheus       # http://localhost:9090
```

Default Grafana login is `admin` / `admin123`. **Change it** before exposing Grafana anywhere ([Security](../about/security.md)).

!!! warning "Dashboard provisioning"
Terraform loads the dashboard from `terraform/modules/monitoring/dashboards/self-healing.json` into a ConfigMap. The Compose setup mounts `monitoring/grafana/dashboards` at `/var/lib/grafana/dashboards`, but `dashboards.yml` expects the files under `/etc/grafana/dashboards/{self-healing,deployments,application,infrastructure}`. If Grafana in Compose shows no dashboards, align those paths or import the JSON files manually (**Dashboards, New, Import**).

## Alerts

Alert rules are in `monitoring/prometheus/alerts.yml` and are routed by Alertmanager (`monitoring/alertmanager/alertmanager.yml`):

| Severity       | Receivers                                                                                             |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `critical`     | PagerDuty and Slack (orchestrator, application and infrastructure criticals have dedicated receivers) |
| `warning`      | Slack                                                                                                 |
| SLO violations | Slack and email                                                                                       |

Highlights (full table with thresholds in the [Configuration Reference](../reference/configuration.md#alert-thresholds)):

- **Deployments:** `HighRollbackRate`, `DeploymentFailureRate`, `DeploymentSuccessRateBelowSLO`, `DeploymentReplicasMismatch`, `PodCrashLooping`
- **Orchestrator:** `OrchestratorDown`, `SlowRollbackDecision`, `HighWebhookFailureRate`, `RedisConnectionFailures`
- **Application:** `HighErrorRate`, `SlowResponseTime`, `ApplicationDown`, memory and CPU
- **Infrastructure and monitoring:** node readiness, disk, scrape failures, Grafana/Loki down

Five rules (`SlowRollbackDecision`, `HighWebhookFailureRate`, `RedisConnectionFailures`, `HighHealthCheckFailureRate`, `SlowDeployment`) query metrics the orchestrator does not emit yet and will stay silent.

Before enabling notifications, replace the placeholders in `alertmanager.yml` (sender address, SMTP host) and mount the Slack webhook URL, SMTP password and PagerDuty keys from Kubernetes Secrets (`/etc/alertmanager/slack_webhook_url`, `smtp_password`).

**Test an alert** safely:

```bash
kubectl -n production scale deployment sample-app --replicas=0
# wait for ApplicationDown (2 min) / DeploymentReplicasMismatch (5 min)
make port-forward-prometheus        # http://localhost:9090/alerts
kubectl -n production scale deployment sample-app --replicas=3
```

## Setting Up the Stack

=== "Terraform (Kubernetes)"

    ```bash
    cd terraform
    terraform apply -var="enable_monitoring=true"
    kubectl get pods -n monitoring
    ```

    Installs `kube-prometheus-stack` (Prometheus, Grafana, Alertmanager, kube-state-metrics, node exporter) and `loki-stack`, with 7-day, 10 GB Prometheus retention.

=== "Docker Compose (local)"

    ```bash
    make dev-start
    # Grafana  http://localhost:3000   Prometheus http://localhost:9090   Loki http://localhost:3100
    ```

    See the caveats above and in [Development Setup](../contributing/development-setup.md#4-docker-compose-stack).

=== "Manual Helm"

    ```bash
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm install prometheus prometheus-community/kube-prometheus-stack \
      -n monitoring --create-namespace --set prometheus.prometheusSpec.retention=7d
    ```

    Then apply the rules and dashboards from `monitoring/`.

### Verify

```bash
# Targets
curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
# Orchestrator metric present?
curl -s 'localhost:9090/api/v1/query?query=deployments_total' | jq '.data.result | length'
# Generate data
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' \
  -d '{"deployment_id":"mon-1","namespace":"staging","app_name":"sample-app","version":"1","status":"success"}'
```

## Best Practices

- **Alert on outcomes, not only components.** Success rate, rollback rate, and `manual_intervention_required` tell you if healing works.
- **Watch MANA itself:** `up{job="orchestrator"}`, pod restarts, and `/health` degradation.
- **Fix the empty-metric gaps** (webhook counters, rollback decision latency, deployment duration) so the shipped alerts and panels become live.
- **Keep cardinality in check.** `retries_total` has a `retry_attempt` label bounded by `MAX_RETRIES`; avoid adding deployment IDs as labels.
- **Retention:** 7 days of metrics and 30 days of logs by default; raise them if you need longer incident history.
- **Secure access** to Grafana and Prometheus, and keep dashboards in Git rather than editing them only in the UI.

## Troubleshooting Monitoring

| Symptom                               | Check                                                                                                                                                           |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Orchestrator target missing or `DOWN` | Pod annotations (manual install) or ServiceMonitor (Terraform); relabeling of `__address__`; port 8000 reachable; use `/metrics/` (a bare `/metrics` redirects) |
| Metrics exist but panels are empty    | The panel uses a recording rule or metric that is not emitted yet; run the underlying query in Prometheus                                                       |
| No dashboards in Grafana              | Provisioning paths (see warning above) or import manually                                                                                                       |
| No logs in Loki                       | Promtail running? Check `kubectl logs -n monitoring -l app=promtail`; query `{namespace="orchestrator"}` first                                                  |
| Alerts never fire                     | Rule loaded (`/rules` page)? Metric present? `for:` duration elapsed?                                                                                           |
| Alerts fire but nothing arrives       | Alertmanager receivers and secrets; check `/#/status` on port 9093                                                                                              |
| Prometheus out of disk                | Reduce retention or raise the volume size (`10Gi` by default)                                                                                                   |
