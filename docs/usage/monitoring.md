# Monitoring and Observability

MANA provides comprehensive monitoring and observability features to help you understand system behavior, detect issues, and maintain operational health.

## Overview of Observability Features

MANA includes three pillars of observability:

1. **Metrics**: Quantitative data collected via Prometheus
2. **Logs**: Qualitative data via structured logging
3. **Traces**: Contextual data for debugging (planned for future releases)

Additionally, MANA provides health checks for both self-monitoring and dependency verification.

## Prometheus Metrics

MANA exposes a comprehensive set of Prometheus metrics at the `/metrics` endpoint (default port 8000).

### Deployment Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `mana_deployments_total` | Counter | Total number of deployments processed | `namespace`, `status` |
| `mana_deployment_duration_seconds` | Histogram | Time taken for deployment processes | `namespace` |
| `mana_active_deployments` | Gauge | Current number of deployments being tracked | `namespace` |
| `mana_deployment_retry_count` | Gauge | Current retry count for a deployment | `namespace`, `deployment_name`, `app_name` |

### Retry and Rollback Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `mana_retries_total` | Counter | Total number of retry attempts | `namespace`, `retry_attempt` |
| `mana_rollbacks_total` | Counter | Total number of rollback operations | `namespace`, `reason` |
| `mana_rollback_duration_seconds` | Histogram | Time taken for rollback operations | `namespace` |
| `mana_rollback_success` | Counter | Number of successful vs failed rollbacks | `namespace`, `success` |

### Health Check Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `mana_health_check_duration_seconds` | Histogram | Time taken for health check operations | `namespace`, `deployment` |
| `mana_health_checks_total` | Counter | Total number of health checks performed | `namespace`, `result` |
| `mana_application_health_failures` | Gauge | Consecutive application health check failures | `namespace`, `deployment_name` |

### System Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `mana_uptime_seconds` | Gauge | How long the orchestrator has been running | None |
| `mana_orchestrator_up` | Gauge | Whether the orchestrator is considered healthy | None |
| `mana_redis_connected` | Gauge | Whether Redis connection is healthy | None |
| `mana_kubernetes_connected` | Gauge | Whether Kubernetes API connection is healthy | None |
| `mana_github_api_available` | Gauge | Whether GitHub API is accessible (when configured) | None |
| `mana_webhook_requests_total` | Counter | Total number of webhook requests received | `endpoint`, `status_code` |
| `mana_webhook_request_duration_seconds` | Histogram | Time taken to process webhook requests | `endpoint` |
| `mana_audit_log_entries_total` | Counter | Total number of audit log entries generated | `action`, `success` |

### Resource Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `mana_orchestrator_memory_usage_bytes` | Gauge | Current memory usage | `state` (usage, cache, etc.) |
| `mana_orchestrator_cpu_usage_seconds_total` | Counter | Total CPU time used | `state` (user, system) |
| `mana_orchestrator_gc_count_total` | Counter | Number of garbage collection cycles | `generation` (0, 1, 2) |
| `mana_orchestrator_thread_count` | Gauge | Number of threads | `state` (active, daemon) |

### Example Prometheus Queries

#### Deployment Health
```promql
# Percentage of successful deployments
sum by (namespace) (mana_deployments_total{status="SUCCESS"})
  / sum by (namespace) (mana_deployments_total)
  * 100

# Deployments currently in failed state
sum by (namespace) (mana_deployments_total{status="FAILED"})
```

#### Retry and Rollback Effectiveness
```promql
# Retry success rate (deployments that succeeded after retry)
sum by (namespace) (mana_deployments_total{status="SUCCESS", retry_count!="0"})
  / sum by (namespace) (mana_deployments_total{retry_count!="0"})
  * 100

# Rollback success rate
sum by (namespace) (mana_rollbacks_total{success="true"})
  / sum by (namespace) (mana_rollbacks_total)
  * 100
```

#### System Health
```promql
# Orchestrator availability
mana_orchestrator_up

# Dependency health
mana_redis_connected * mana_kubernetes_connected

# Resource utilization (percentage)
(mana_orchestrator_memory_usage_bytes /
  (mana_orchestrator_memory_usage_bytes + mana_orchestrator_memory_available_bytes))
  * 100

(mana_orchestrator_cpu_usage_seconds_total /
  (mana_orchestrator_cpu_usage_seconds_total + mana_orchestrator_cpu_available_seconds_total))
  * 100
```

#### Performance Metrics
```promql
# Average deployment duration
avg_over_time(mana_deployment_duration_seconds[5m])

# 95th percentile deployment duration
histogram_quantile(0.95,
  sum by (le, namespace) (rate(mana_deployment_duration_seconds_bucket[5m]))
)

# Average health check duration
avg_over_time(mana_health_check_duration_seconds[5m])

# Health check success rate
sum by (namespace) (mana_health_checks_total{result="healthy"})
  / sum by (namespace) (mana_health_checks_total)
  * 100
```

#### System Load
```promql
# Request rate to orchestrator
rate(mana_webhook_requests_total[5m])

# Audit log generation rate
rate(mana_audit_log_entries_total[5m])

# Error rate in webhook processing
sum by (endpoint) (rate(mana_webhook_requests_total{status_code=~"5.."}[5m]))
  / sum by (endpoint) (rate(mana_webhook_requests_total[5m]))
  * 100
```

## Structured Logging

MANA uses structured JSON logging for all audit events and operational logs. This makes logs easy to parse, search, and analyze with tools like Elasticsearch, Splunk, Loki, or even simple grep/jq combinations.

### Log Format
Each log entry follows this format:
```json
{
  "timestamp": "2023-11-01T14:30:00.123Z",
  "level": "info",
  "service": "self-healing-orchestrator",
  "message": "Human readable message",
  "logger": "module.name",
  "trace_id": "optional-trace-id",
  "span_id": "optional-span-id",
  "fields": {
    // Structured data specific to the log entry
  }
}
```

### Audit Log Entries
Audit logs are a special category of structured logs with the `logger` field set to `self-healing-orchestrator.audit` and containing detailed action information:

```json
{
  "timestamp": "2023-11-01T14:30:00.123Z",
  "level": "info",
  "service": "self-healing-orchestrator",
  "message": "AUDIT: failure_detected",
  "logger": "self-healing-orchestrator.audit",
  "trace_id": "abc123",
  "span_id": "def456",
  "fields": {
    "audit_event": "{\"timestamp\":\"2023-11-01T14:30:00Z\",\"service\":\"self-healing-orchestrator\",\"action\":\"failure_detected\",\"success\":true,\"deployment_id\":\"abc123-def456-ghi789\",\"namespace\":\"production\",\"deployment_name\":\"web-app\",\"version\":\"1.2.3\",\"details\":{\"failure_type\":\"health_check_failure\",\"retry_count\":2}}"
  }
}
```

The `audit_event` field contains a JSON string with the complete audit event data.

### Log Levels
MANA uses standard Python logging levels:
- **DEBUG**: Detailed information, typically of interest only when diagnosing problems
- **INFO**: Confirmation that things are working as expected
- **WARNING**: An indication that something unexpected happened, or indicative of some problem in the near future
- **ERROR**: Due to a more serious problem, the software has not been able to perform some function
- **CRITICAL**: A serious error, indicating that the program itself may be unable to continue running

### Log Collection Strategies
MANA is designed to work well with common log collection approaches:

#### Sidecar Pattern
Deploy a logging agent (like Fluent Bit or Filebeat) as a sidecar container to collect logs from the orchestrator container.

#### DaemonSet Pattern
Deploy a logging agent as a DaemonSet on each node to collect logs from all containers.

#### Application-Level Logging
Configure your logging framework to send logs directly to a central logging service.

### Example Log Queries (using Loki/Promtail or similar)

#### Find all failures for a specific deployment
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.action="failure_detected" | audit_event.deployment_name="web-app"
```

#### Show rollback operations
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.action=~"rollback_.*"
```

#### Show health check failures
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.action="health_check_failed"
```

#### Show manual intervention requirements
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.action="manual_intervention_required"
```

#### Show all successful operations
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.success=true
```

#### Show all failed operations
```
{logger="self-healing-orchestrator.audit"} | json | audit_event.success=false
```

#### Find logs for a specific time range
```
{logger="self-healing-orchestrator.audit"} | json | timestamp >= "2023-11-01T00:00:00Z" and timestamp <= "2023-11-01T23:59:59Z"
```

## Health Checks

MANA provides several health check endpoints for monitoring both the orchestrator itself and the applications it manages.

### Orchestrator Health Check
The `/health` endpoint reports the health of the MANA orchestrator service and its critical dependencies:

```bash
curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/health
```

Sample response:
```json
{
  "status": "healthy",
  "redis": "healthy",
  "kubernetes": "healthy",
  "github_integration": true,
  "timestamp": "2023-11-01T14:30:00Z"
}
```

#### Health Status Values
- `healthy`: All dependencies are healthy
- `degraded`: One or more dependencies are unhealthy but service can still operate in limited capacity
- `unhealthy`: Critical failure preventing normal operation

#### Dependency Health
- `redis`: Health of Redis connection (critical for state persistence)
- `kubernetes`: Health of Kubernetes API connection (critical for all operations)
- `github_integration`: Whether GitHub API is accessible (only relevant if retry functionality is configured)

### Deployment Health Check
The `/health-check` endpoint allows you to check the health of a specific deployment as MANA sees it:

```bash
curl -X POST http://mana-orchestrator.<namespace>.svc.cluster.local:8000/health-check \
  -H "Content-Type: application/json" \
  -d '{
    "namespace": "production",
    "deployment_name": "web-app",
    "timeout": 300
  }'
```

Sample response:
```json
{
  "deployment_name": "web-app",
  "namespace": "production",
  "healthy": true,
  "check_duration_seconds": 1.23
}
```

This endpoint performs the same health check logic that MANA uses internally, making it useful for verifying that MANA's view of deployment health matches your expectations.

### Readiness and Liveness Probes
MANA includes standard Kubernetes liveness and readiness probes in its deployment manifest:

#### Liveness Probe
- **Purpose**: Determine if the container is running and should be restarted
- **Check**: `/health` endpoint
- **Failure Threshold**: 3 consecutive failures
- **Restart Policy**: Kubernetes will restart the container

#### Readiness Probe
- **Purpose**: Determine if the container is ready to serve traffic
- **Check**: `/health` endpoint
- **Failure Threshold**: 3 consecutive failures
- **Behavior**: Kubernetes will remove the pod from service endpoints

## Distributed Tracing (Planned)

While not implemented in the current version, MANA is designed to support distributed tracing in future releases through:

- **Trace ID Propagation**: Passing trace IDs through HTTP headers
- **Span Creation**: Creating spans for significant operations
- **Integration with Tracing Systems**: Compatibility with Jaeger, Zipkin, or AWS X-Ray
- **Context Propagation**: Ensuring trace context is maintained across asynchronous operations

### Planned Trace Context
When implemented, MANA would propagate:
- `trace_id`: Unique identifier for the entire trace
- `span_id`: Identifier for the current span
- `parent_span_id`: Identifier for the parent span (if any)
- `trace_state`: Additional trace state information
- `trace_flags`: Flags indicating sampling decisions and other traits

## Dashboards and Visualization

While MANA doesn't provide pre-built dashboards, its metrics and logs are designed to work well with common observability stacks.

### Prometheus + Grafana
MANA's metrics work natively with Prometheus and Grafana. Here are some suggested dashboard panels:

#### Deployment Overview Panel
- Stat: Total deployments processed (success/failed/in_progress)
- Graph: Deployment success rate over time
- Table: Recent deployments with status and version
- Heatmap: Deployment duration distribution

#### Retry and Rollback Panel
- Stat: Total retries and rollbacks
- Graph: Retry success rate over time
- Pie chart: Rollback reasons
- Table: Recent rollback operations

#### System Health Panel
- Stat: Orchestrator uptime
- Gauge: Resource utilization (CPU, memory)
- Stat: Dependency health (Redis, Kubernetes, GitHub)
- Graph: Request rate over time
- Table: Recent error occurrences

#### Health Check Panel
- Graph: Health check success rate over time
- Histogram: Health check duration distribution
- Table: Recent health check failures
- Stat: Current application failure counts

### Loki + Grafana
MANA's structured logs work well with Loki for log aggregation and Grafana for visualization:

#### Log Panels
- Table: Recent audit log entries (with expandable details)
- Bar chart: Audit log entries by action type
- Time series: Audit log generation rate
- Table: Failed audit log entries (success=false)
- Table: Manual intervention required entries

#### Search Panels
- Free text search across all log fields
- Filter by deployment ID, namespace, or version
- Filter by action type and success/failure status
- Date/time range selection

## Setting Up Monitoring

### Basic Prometheus Setup
If you're using the Prometheus Operator or similar:

```yaml
# ServiceMonitor for MANA (if using Prometheus Operator)
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: mana
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: mana-orchestrator
  namespace: mana
  endpoints:
  - port: http
    interval: 30s
    path: /metrics
```

### Basic Loki Setup
For log collection with Loki:

```yaml
# Promtail configuration snippet for MANA logs
scrape_configs:
- job_name: kubernetes-pods-name
  pipeline_stages:
  - json:
      expressions:
        level: level
        service: service
        message: message
        logger: logger
        trace_id: trace_id
        span_id: span_id
        fields: fields
  - json:
      expressions:
        timestamp: timestamp
        service: service
        action: action
        success: success
        deployment_id: deployment_id
        namespace: namespace
        deployment_name: deployment_name
        version: version
        details: details
    source: fields.audit_event
  - output:
      source: fields.timestamp
  - timestamp:
      source: source
  relabel_configs:
  - source_labels: [__meta_kubernetes_pod_label_app]
    action: keep
    regex: mana-orchestrator
```

### Basic Grafana Dashboard JSON
Here's a simple example of what a Grafana dashboard JSON might look like for MANA:

```json
{
  "annotations": {
    "list": [
      {
        "builtIn": 1,
        "datasource": "-- Grafana --",
        "enable": true,
        "hide": true,
        "iconColor": "rgba(0, 211, 255, 0.9)",
        "label": "Alerts",
        "type": "dashboard"
      }
    ]
  },
  "editable": true,
  "gnetId": null,
  "graphTooltip": 0,
  "id": null,
  "links": [],
  "panels": [
    {
      "datasource": "Prometheus",
      "description": "Deployment Success Rate",
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "palette-classic"
          },
          "custom": {}
        },
        "overrides": []
      },
      "gridPos": {
        "h": 4,
        "w": 12,
        "x": 0,
        "y": 0
      },
      "id": 1,
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "single"
        }
      },
      "pluginVersion": "8.5.2",
      "targets": [
        {
          "expr": "sum by (namespace) (mana_deployments_total{status=\"SUCCESS\"}) / sum by (namespace) (mana_deployments_total) * 100",
          "interval": "",
          "legendFormat": "{{namespace}} Success Rate",
          "refId": "A"
        }
      ],
      "title": "Deployment Success Rate",
      "type": "stat"
    },
    {
      "datasource": "Prometheus",
      "description": "System Health",
      "fieldConfig": {
        "defaults": {
          "color": {
            "mode": "palette-classic"
          },
          "custom": {}
        },
        "overrides": []
      },
      "gridPos": {
        "h": 4,
        "w": 12,
        "x": 12,
        "y": 0
      },
      "id": 2,
      "options": {
        "reduceOptions": {
          "calcs": [
            "lastNotNull"
          ],
          "values": false
          },
          "query": {
            "params": [
              "B"
            ]
          }
        },
        "options": {
          "legend": {
            "displayMode": "list",
            "placement": "bottom"
          },
          "tooltip": {
            "mode": "single"
          }
        }
      },
      "pluginVersion": "8.5.2",
      "targets": [
        {
          "expr": "mana_redis_connected * mana_kubernetes_connected",
          "interval": "",
          "legendFormat": "Dependencies Healthy",
          "refId": "A"
        }
      ],
      "title": "System Health",
      "type": "stat"
    }
  ],
  "refresh": "30s",
  "schemaVersion": 38,
  "style": "dark",
  "tags": [
    "mana",
    "monitoring"
  ],
  "templating": {
    "list": [
      {
        "current": {
          "text": "All",
          "value": "$__all",
          "query": "mana",
          "type": "query"
        },
        "datasource": {
          "type": "prometheus",
          "name": "PS1",
          "url": ""
        },
        "hide": 0,
        "label": "Namespace",
        "name": "namespace",
        "options": [
          {
            "selected": true,
            "text": "All",
            "value": "$__all"
          }
        ],
        "refresh": 1,
        "sort": 1,
        "type": "query"
      }
    ]
  },
  "time": {
    "from": "now-1h",
    "to": "now"
  },
  "timepicker": {
    "refresh_intervals": [
      "10s",
      "30s",
      "1m",
      "5m",
      "15m",
      "30m",
      "1h",
      "2h",
      "1 day"
    ],
    "time_options": [
      "5m",
      "15m",
      "30m",
      "1h",
      "2h",
      "1h",
      "6h",
      "12h",
      "24h",
      "2d",
      "7d",
      "30d"
    ]
  },
  "timezone": "",
  "title": "MANA Overview",
  "uid": "mana-overview",
  "version": 1,
  "weekStart": ""
}
```

## Best Practices for Monitoring

### 1. Monitor the Four Golden Signals
Focus on these key metrics for system health:
- **Latency**: `mana_deployment_duration_seconds`, `mana_health_check_duration_seconds`, `mana_webhook_request_duration_seconds`
- **Traffic**: `mana_webhook_requests_total`, `mana_audit_log_entries_total`
- **Errors**: `mana_deployments_total{status!="SUCCESS"}`, `mana_rollbacks_total`, `mana_webhook_requests_total{status_code=~"[45].."}`
- **Saturation**: `mana_orchestrator_memory_usage_bytes`, `mana_orchestrator_cpu_usage_seconds_total`, `mana_active_deployments`

### 2. Set Up Alerting
Consider alerts for:
- **High error rates**: Sudden increase in failed deployments or rollbacks
- **Performance degradation**: Increasing latencies over time
- **Resource exhaustion**: Approaching CPU or memory limits
- **Dependency failures**: Redis or Kubernetes API unavailability
- **Manual intervention requirements**: When automatic recovery is exhausted
- **Orchestrator unavailability**: Multiple pods crashing or not responding

### 3. Use Appropriate Time Windows
Different metrics benefit from different observation windows:
- **Real-time (seconds-minutes)**: Alerting, troubleshooting active issues
- **Short-term (minutes-hours)**: Trend analysis, capacity planning
- **Medium-term (hours-days)**: Weekly reports, pattern identification
- **Long-term (weeks-months)**: Monthly reports, seasonal adjustments, capacity forecasting

### 4. Correlate Across Observability Pillars
When investigating an issue:
1. Start with metrics to identify what's wrong
2. Use logs to understand why it's happening
3. Use traces (when available) to see exactly how it happened
4. Verify with health checks to confirm current state

### 5. Monitor Business-Level Metrics
Don't just monitor technical metrics:
- Track deployment frequency over time
- Measure MTTR (Mean Time To Recovery) for failures
- Monitor rollback frequency as an indicator of release quality
- Track the percentage of failures resolved by retry vs. requiring rollback
- Measure the effectiveness of different failure types handling

### 6. Create Service-Level Objectives (SLOs)
Define and track SLOs for your deployment system:
- **Availability**: Percentage of time the orchestrator is healthy
- **Latency**: 95th percentile deployment health check time
- **Error Rate**: Percentage of deployments requiring manual intervention
- **Recovery Time**: Average time from failure detection to recovery
- **Release Quality**: Percentage of deployments that succeed on first attempt

### 7. Regularly Review and Tune
Monitoring is not a set-and-forget activity:
- Review dashboards weekly for anomalies
- Tune alert thresholds based on observed baselines
- Add new metrics as you identify new failure modes
- Remove noisy or unactionable alerts
- Update monitoring as your system evolves

## Troubleshooting Monitoring Issues

### No Metrics Appearing in Prometheus
**Checks**:
- Verify the `/metrics` endpoint is accessible: `curl http://<mana-ip>:<port>/metrics`
- Check if ServiceMonitor or PodMonitor is configured correctly
- Ensure Prometheus can reach the mana service (network policies, services)
- Verify the mana service is exposing the correct port
- Check if metrics are being filtered or transformed incorrectly

### Missing or Incorrect Labels
**Checks**:
- Verify the mana service is exposing metrics with correct labels
- Check if Prometheus is scraping from the correct endpoint
- Ensure relabeling configs aren't removing or altering labels unexpectedly
- Look for typos in label names in your queries or dashboard

### Delayed or Stale Metrics
**Checks**:
- Check the scrape interval in your Prometheus configuration
- Verify the mana service is actually updating metrics
- Look for blocking operations that might prevent metric updates
- Check if the orchestrator is stuck or in a degraded state

### High Cardinality Issues
**Checks**:
- Monitor the number of unique time series being generated
- Look for high-cardinality labels like `deployment_id` or `timestamp` used as labels
- Consider using histograms and summaries instead of gauges for high-cardinality data
- Evaluate whether you really need to monitor every single deployment individually

### Log Collection Problems
**Checks**:
- Verify log collection agents are running and healthy
- Check if logs are being formatted correctly as JSON
- Ensure timestamp parsing is working correctly
- Look for resource limits on log collection agents causing data loss
- Check network connectivity between log sources and collection agents

### Dashboard Issues
**Checks**:
- Verify the data source is correctly configured and accessible
- Check if queries are returning expected results when run directly in Explore
- Look for timeout settings that might be cutting off long-running queries
- Check if panel-specific transforms are altering data unexpectedly
- Ensure time ranges are set appropriately for the data being displayed

## Next Steps

After setting up monitoring for MANA:

1. [Review the API Reference](reference/api.md) for programmatic access to metrics and status
2. [Explore Advanced Configuration](configuration/overview.md) to tune monitoring-related settings
3. [Read the Troubleshooting Guide](usage/troubleshooting.md) for help with common issues
4. [Explore Contributing Guidelines](contributing/contributing.md) if you wish to improve MANA's monitoring capabilities
5. [Study the Architecture](architecture/overview.md) to understand how monitoring is integrated into the system

Effective monitoring turns MANA from a black box into a transparent system where you can understand what's happening, why it's happening, and what you can do to improve it.