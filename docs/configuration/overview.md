# Configuration Guide

MANA is designed to be highly configurable through environment variables, Helm values, or direct configuration files. This document explains how to configure MANA for your specific environment and use cases.

## Configuration Methods

MANA supports three primary configuration methods:

### 1. Environment Variables (Recommended for Production)
Environment variables are the primary way to configure MANA in production environments, especially when using Kubernetes.

```bash
# Example environment variables
export REDIS_HOST=redis-service
export REDIS_PORT=6379
export MAX_RETRIES=3
export GITHUB_TOKEN=your_github_token
export GITHUB_REPO=your-org/your-repo
```

### 2. Helm Values File (Recommended for Helm Installations)
When using the Helm chart, you can provide a custom values file:

```yaml
# values.yaml
replicaCount: 3
github:
  token: ""  # Set via Kubernetes secret
  repo: "your-org/your-repo"
redis:
  enabled: true
  architecture: standalone
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
```

### 3. Direct Configuration File (For Manual Deployments)
When deploying manually, you can modify the configuration directly in the source code or use a config file.

## Configuration Categories

MANA's configuration is organized into several logical categories:

### Redis Configuration
Settings for connecting to the Redis instance used for state persistence.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `REDIS_HOST` | Redis server hostname | `localhost` | Yes |
| `REDIS_PORT` | Redis server port | `6379` | Yes |
| `REDIS_PASSWORD` | Redis password (if auth enabled) | `""` | No |
| `REDIS_SSL` | Whether to use SSL/TLS connection | `false` | No |
| `REDIS_SSL_CERT_REQS` | SSL certificate requirements | `none` | No |
| `REDIS_HEALTH_CHECK_INTERVAL` | Health check interval in seconds | `30` | No |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | Connection timeout in seconds | `5` | No |
| `REDIS_SOCKET_TIMEOUT` | Read/write timeout in seconds | `5` | No |
| `REDIS_RETRY_ON_TIMEOUT` | Whether to retry on timeout | `false` | No |
| `REDIS_MAX_CONNECTIONS` | Maximum number of connections | `20` | No |
| `REDIS_HEALTH_CHECK_ENABLED` | Enable Redis health checks | `true` | No |

### Retry and Rollback Configuration
Settings controlling MANA's retry and rollback behavior.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `MAX_RETRIES` | Maximum number of retry attempts before rollback | `3` | Yes |
| `ROLLBACK_THRESHOLD` | Number of retries after which rollback is triggered | `2` | Yes |
| `INITIAL_BACKOFF` | Initial backoff delay in seconds | `10.0` | Yes |
| `MAX_BACKOFF` | Maximum backoff delay in seconds | `300.0` | Yes |
| `BACKOFF_MULTIPLIER` | Multiplier for exponential backoff | `2.0` | Yes |
| `JITTER_ENABLED` | Whether to add jitter to backoff | `true` | No |
| `JITTER_RANGE` | Jitter range as percentage of backoff | `0.1` (10%) | No |

### Health Check Configuration
Settings for how MANA checks application and deployment health.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `HEALTH_CHECK_TIMEOUT` | Timeout for health check HTTP requests in seconds | `5` | Yes |
| `HEALTH_CHECK_PORT` | Port to use for health checks | `5000` | Yes |
| `HEALTH_CHECK_PATH` | Path to use for health checks | `/health` | Yes |
| `HEALTH_CHECK_METHOD` | HTTP method for health checks | `GET` | Yes |
| `HEALTH_CHECK_HEADERS` | Custom headers for health checks (JSON) | `"{}"` | No |
| `HEALTH_CHECK_EXPECTED_STATUS` | Expected HTTP status for healthy response | `200` | Yes |
| `HEALTH_CHECK_EXPECTED_RESPONSE` | Expected JSON response for healthy state | `'{"status":"healthy"}'` | No |
| `MAX_APP_HEALTH_FAILURES` | Max consecutive app health failures before marking unhealthy | `3` | Yes |
| `HEALTH_CHECK_INTERVAL` | Interval between health checks in seconds | `10` | No |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | Overall timeout for deployment health checks | `300` | Yes |

### GitHub Integration Configuration
Settings for integrating with GitHub Actions for workflow re-runs.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `GITHUB_TOKEN` | GitHub personal access token with `actions:write` scope | `""` | No (but required for retry functionality) |
| `GITHUB_REPO` | GitHub repository in format "owner/repo" | `""` | No (but required for retry functionality) |
| `GITHUB_API_BASE_URL` | Base URL for GitHub API | `https://api.github.com` | No |
| `GITHUB_WEBHOOK_SECRET` | Secret for validating GitHub webhooks (if applicable) | `""` | No |
| `GITHUB_REQUEST_TIMEOUT` | Timeout for GitHub API requests in seconds | `10` | No |
| `GITHUB_MAX_RETRIES` | Maximum retries for GitHub API requests | `3` | No |
| `GITHUB_RETRY_BACKOFF_FACTOR` | Backoff factor for GitHub API retries | `0.3` | No |

### Orchestrator Service Configuration
Settings for the MANA orchestrator service itself.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `PORT` | Port for the orchestrator API to listen on | `8000` | Yes |
| `HOST` | Host for the orchestrator API to bind to | `0.0.0.0` | Yes |
| `WORKERS` | Number of worker processes (for Gunicorn/uvicorn workers) | `1` | No |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `INFO` | Yes |
| `LOG_FORMAT` | Log format (`text` or `json`) | `text` | No |
| `ENABLE_AUDIT_LOGGING` | Whether to enable audit logging | `true` | Yes |
| `AUDIT_LOG_RETENTION_DAYS` | Number of days to retain audit logs | `30` | No |
| `METRICS_ENABLED` | Whether to enable Prometheus metrics | `true` | Yes |
| `METRICS_PORT` | Port for Prometheus metrics endpoint | `8000` (same as main API) | Yes |
| `ENABLE_CORS` | Whether to enable CORS for the API | `false` | No |
| `CORS_ORIGINS` | Allowed origins for CORS (JSON array) | `"[]"` | No |
| `REQUEST_ID_HEADER` | Header name for request ID tracing | `X-Request-ID` | No |
| `ENABLE_REQUEST_ID_LOGGING` | Whether to include request ID in logs | `true` | No |

### Resource Configuration
Settings for Kubernetes resource requests and limits.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `RESOURCE_REQUESTS_MEMORY` | Memory request for orchestrator container | `256Mi` | Yes |
| `RESOURCE_REQUESTS_CPU` | CPU request for orchestrator container | `100m` | Yes |
| `RESOURCE_LIMITS_MEMORY` | Memory limit for orchestrator container | `512Mi` | Yes |
| `RESOURCE_LIMITS_CPU` | CPU limit for orchestrator container | `500m` | Yes |
| `RESOURCE_REQUESTS_MEMORY_REDIS` | Memory request for Redis container | `64Mi` | Yes (if using deployed Redis) |
| `RESOURCE_REQUESTS_CPU_REDIS` | CPU request for Redis container | `50m` | Yes (if using deployed Redis) |
| `RESOURCE_LIMITS_MEMORY_REDIS` | Memory limit for Redis container | `128Mi` | Yes (if using deployed Redis) |
| `RESOURCE_LIMITS_CPU_REDIS` | CPU limit for Redis container | `100m` | Yes (if using deployed Redis) |

### Advanced Configuration
Additional settings for specialized use cases.

| Environment Variable | Description | Default | Required |
|----------------------|-------------|---------|----------|
| `NAMESPACE_LABEL_SELECTOR` | Label selector for namespaces to monitor | `""` (all namespaces) | No |
| `DEPLOYMENT_LABEL_SELECTOR` | Label selector for deployments to monitor | `""` (all deployments) | No |
| `IGNORE_NAMESPACES` | Comma-separated list of namespaces to ignore | `""` | No |
| `IGNORE_DEPLOYMENTS` | Comma-separated list of deployments to ignore | `""` | No |
| `PROCESS_EXISTING_DEPLOYMENTS` | Whether to process existing deployments on startup | `true` | No |
| `STATE_TTL_SECONDS` | TTL for deployment states in Redis (seconds) | `86400` (24 hours) | No |
| `CLEANUP_OLD_STATES_INTERVAL` | Interval for cleaning up old states in seconds | `3600` (1 hour) | No |
| `MAX_CONCURRENT_HEALTH_CHECKS` | Maximum concurrent health checks | `10` | No |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | Timeout for individual health checks | `5` | Yes |
| `ENABLE_LEADER_ELECTION` | Whether to enable leader election for HA | `false` | No |
| `LEADER_ELECTION_LEASE_DURATION` | Leader election lease duration in seconds | `15` | No |
| `LEADER_ELECTION_RENEW_DEADLINE` | Leader election renew deadline in seconds | `10` | No |
| `LEADER_ELECTION_RETRY_PERIOD` | Leader election retry period in seconds | `2` | No |
| `WEBHOOK_AUTH_ENABLED` | Whether to require authentication for webhooks | `false` | No |
| `WEBHOOK_AUTH_TOKEN` | Token required for webhook authentication | `""` | No |
| `WEBHOOK_RATE_LIMIT_ENABLED` | Whether to enable rate limiting on webhooks | `false` | No |
| `WEBHOOK_RATE_LIMIT_REQUESTS` | Number of requests allowed per window | `100` | No |
| `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS` | Rate limit window in seconds | `60` | No |

## Configuration Validation

MANA validates configuration values at startup to ensure they are within acceptable ranges. Common validation rules include:

- Numeric values must be within valid ranges (e.g., ports 1-65535, positive numbers for timeouts and counts)
- Ratios and multipliers must be greater than specific thresholds (e.g., `BACKOFF_MULTIPLIER` > 1.0)
- Passwords and tokens must meet minimum length requirements when provided
- URLs must be valid and accessible
- JSON values must be properly formatted

If validation fails, MANA will log detailed error messages and exit during startup.

## Environment-Specific Configuration

### Development Environment

For development and testing, you might use:

```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
export MAX_RETRIES=2
export ROLLBACK_THRESHOLD=1
export INITIAL_BACKOFF=5.0
export MAX_BACKOFF=60.0
export LOG_LEVEL=DEBUG
export ENABLE_AUDIT_LOGGING=true
export METRICS_ENABLED=true
```

### Staging Environment

For staging environments that mirror production:

```bash
export REDIS_HOST=redis-staging.example.com
export REDIS_PORT=6379
export REDIS_PASSWORD=${REDIS_PASSWORD_STAGING}
export MAX_RETRIES=3
export ROLLBACK_THRESHOLD=2
export INITIAL_BACKOFF=15.0
export MAX_BACKOFF=300.0
export LOG_LEVEL=INFO
export ENABLE_AUDIT_LOGGING=true
export METRICS_ENABLED=true
export GITHUB_TOKEN=${GITHUB_TOKEN_STAGING}
export GITHUB_REPO=your-org/your-repo-staging
```

### Production Environment

For production environments with high availability requirements:

```bash
export REDIS_HOST=redis-cluster.example.com
export REDIS_PORT=6379
export REDIS_PASSWORD=${REDIS_PASSWORD_PROD}
export REDIS_SSL=true
export MAX_RETRIES=5
export ROLLBACK_THRESHOLD=3
export INITIAL_BACKOFF=30.0
export MAX_BACKOFF=600.0
export BACKOFF_MULTIPLIER=3.0
export LOG_LEVEL=WARNING
export ENABLE_AUDIT_LOGGING=true
export AUDIT_LOG_RETENTION_DAYS=90
export METRICS_ENABLED=true
export METRICS_PORT=9090
export ENABLE_LEADER_ELECTION=true
export GITHUB_TOKEN=${GITHUB_TOKEN_PROD}
export GITHUB_REPO=your-org/your-repo
export RESOURCE_REQUESTS_MEMORY=512Mi
export RESOURCE_REQUESTS_CPU=250m
export RESOURCE_LIMITS_MEMORY=1Gi
export RESOURCE_LIMITS_CPU=500m
```

## Configuration Best Practices

### 1. Use Environment Variables for Secrets
Never hardcode sensitive information like GitHub tokens or passwords in configuration files. Always use environment variables or Kubernetes secrets.

### 2. Separate Environment-Specific Config
Use different configuration files or environment variable sets for different environments (dev, staging, prod).

### 3. Validate Before Applying
Test your configuration in a non-production environment before applying to production.

### 4. Use Version Control for Configs
Keep your configuration files in version control (excluding secrets) to track changes over time.

### 5. Document Non-Obvious Settings
Add comments to your configuration files explaining why certain non-default values were chosen.

### 6. Regularly Review and Tune
Periodically review your configuration settings based on operational data and adjust as needed.

### 7. Use Secrets Management Tools
In production, consider using Kubernetes secrets, HashiCorp Vault, AWS Secrets Manager, or similar tools for managing sensitive configuration.

### 8. Avoid Over-Provisioning
Start with conservative resource requests and limits, then adjust based on actual usage metrics.

### 9. Monitor Configuration Changes
Implement alerts for unexpected configuration changes in your environment.

### 10. Test Configuration Changes
In non-production environments, test configuration changes before applying to production.

## Example Configuration Files

### Minimal Development Configuration (.env.development)
```bash
# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Retry/Rollback
MAX_RETRIES=2
ROLLBACK_THRESHOLD=1
INITIAL_BACKOFF=5.0
MAX_BACKOFF=60.0
BACKOFF_MULTIPLIER=2.0

# Health Checks
HEALTH_CHECK_TIMEOUT=5
HEALTH_CHECK_PORT=5000
HEALTH_CHECK_PATH=/health
MAX_APP_HEALTH_FAILURES=3

# Orchestrator
PORT=8000
LOG_LEVEL=DEBUG
ENABLE_AUDIT_LOGGING=true
METRICS_ENABLED=true
```

### Production Configuration (.env.production)
```bash
# Redis (external cluster)
REDIS_HOST=redis-cluster.example.com
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_SSL=true
REDIS_SSL_CERT_REQS=required

# Retry/Rollback
MAX_RETRIES=5
ROLLBACK_THRESHOLD=3
INITIAL_BACKOFF=30.0
MAX_BACKOFF=600.0
BACKOFF_MULTIPLIER=3.0
JITTER_ENABLED=true
JITTER_RANGE=0.1

# Health Checks
HEALTH_CHECK_TIMEOUT=10
HEALTH_CHECK_PORT=5000
HEALTH_CHECK_PATH=/health
HEALTH_CHECK_METHOD=GET
HEALTH_CHECK_HEADERS={"Authorization":"Bearer ${HEALTH_CHECK_TOKEN}"}
MAX_APP_HEALTH_FAILURES=5
HEALTH_CHECK_INTERNAL=15

# Orchestrator
PORT=8000
HOST=0.0.0.0
LOG_LEVEL=WARNING
ENABLE_AUDIT_LOGGING=true
AUDIT_LOG_RETENTION_DAYS=90
METRICS_ENABLED=true
METRICS_PORT=9090
ENABLE_LEADER_ELECTION=true

# GitHub Integration
GITHUB_TOKEN=${GITHUB_TOKEN}
GITHUB_REPO=your-org/your-repo
GITHUB_REQUEST_TIMEOUT=15
GITHUB_MAX_RETRIES=3

# Resources
RESOURCE_REQUESTS_MEMORY=512Mi
RESOURCE_REQUESTS_CPU=250m
RESOURCE_LIMITS_MEMORY=1Gi
RESOURCE_LIMITS_CPU=500m

# Advanced Features
NAMESPACE_LABEL_SELECTOR=environment in (production,staging)
STATE_TTL_SECONDS=86400
CLEANUP_OLD_STATES_INTERVAL=3600
WEBHOOK_RATE_LIMIT_ENABLED=true
WEBHOOK_RATE_LIMIT_REQUESTS=100
WEBHOOK_RATE_LIMIT_WINDOW_SECONDS=60
```

### Helm Values File (production-values.yaml)
```yaml
replicaCount: 3

image:
  repository: othaime-en/mana
  pullPolicy: IfNotPresent
  tag: "latest"

github:
  token: ""  # Set via Kubernetes secret
  repo: "your-org/your-repo"
  requestTimeout: 15
  maxRetries: 3

redis:
  enabled: false  # Using external Redis
  external:
    host: redis-cluster.example.com
    port: 6379
    password: ""  # Set via Kubernetes secret
    ssl: true

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80

resources:
  requests:
    memory: 512Mi
    cpu: 250m
  limits:
    memory: 1Gi
    cpu: 500m

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10

podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8000"

podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 2000

securityContext:
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 1000

service:
  type: ClusterIP
  port: 8000

ingress:
  enabled: false
  className: ""
  annotations: {}
  hosts:
    - host: mana.example.com
      paths:
        - path: /
          pathType: Prefix
  tls: []

# Configuration via environment variables
env:
  - name: LOG_LEVEL
    value: "WARNING"
  - name: ENABLE_AUDIT_LOGGING
    value: "true"
  - name: AUDIT_LOG_RETENTION_DAYS
    value: "90"
  - name: METRICS_ENABLED
    value: "true"
  - name: METRICS_PORT
    value: "9090"
  - name: ENABLE_LEADER_ELECTION
    value: "true"
  - name: STATE_TTL_SECONDS
    value: "86400"
  - name: CLEANUP_OLD_STATES_INTERVAL
    value: "3600"
  - name: WEBHOOK_RATE_LIMIT_ENABLED
    value: "true"
  - name: WEBHOOK_RATE_LIMIT_REQUESTS
    value: "100"
  - name: WEBHOOK_RATE_LIMIT_WINDOW_SECONDS
    value: "60"
```

## Configuration Tips and Tricks

### Using Kubernetes Secrets for Sensitive Data
Instead of passing sensitive data directly as environment variables, use Kubernetes secrets:

```yaml
# Create a secret
kubectl create secret generic mana-secrets \
  --namespace mana \
  --from-literal=github-token=${GITHUB_TOKEN} \
  --from-literal=redis-password=${REDIS_PASSWORD}

# Reference in Helm values
github:
  tokenFrom:
    secretKeyRef:
      name: mana-secrets
      key: github-token
redis:
  external:
    passwordFrom:
      secretKeyRef:
        name: mana-secrets
        key: redis-password
```

### Using ConfigMaps for Non-Sensitive Configuration
For non-sensitive configuration that you might want to change without rebuilding images:

```yaml
# Create a ConfigMap
kubectl create configmap mana-config \
  --namespace mana \
  --from-literal=log-level=WARNING \
  --from-literal=enable-audit-logging=true \
  --from-literal=max-retries=5

# Reference in deployment
env:
  - name: LOG_LEVEL
    valueFrom:
      configMapKeyRef:
        name: mana-config
        key: log-level
  - name: ENABLE_AUDIT_LOGGING
    valueFrom:
      configMapKeyRef:
        name: mana-config
        key: enable-audit-logging
  - name: MAX_RETRIES
    valueFrom:
      configMapKeyRef:
        name: mana-config
        key: max-retries
```

### Parameter Overrides in CI/CD Pipelines
You can override configuration values in your CI/CD pipeline for different deployment targets:

```yaml
# In your CI/CD pipeline (example for GitHub Actions)
- name: Deploy to Staging
  run: |
    helm upgrade mana ./orchestrator/charts/mana \
      --namespace mana-staging \
      --set github.token=${{ secrets.GITHUB_TOKEN_STAGING }} \
      --set github.repo=your-org/your-repo-staging \
      -f staging-values.yaml

- name: Deploy to Production
  run: |
    helm upgrade mana ./orchestrator/charts/mana \
      --namespace mana-production \
      --set github.token=${{ secrets.GITHUB_TOKEN_PROD }} \
      --set github.repo=your-org/your-repo \
      -f production-values.yaml
```

### Feature Flags and Toggles
Use environment variables to enable/disable features:

```bash
# Disable GitHub integration (for environments without GitHub)
export GITHUB_TOKEN=""
export GITHUB_REPO=""

# Disable audit logging (not recommended for production)
export ENABLE_AUDIT_LOGGING=false

# Disable metrics (not recommended)
export METRICS_ENABLED=false

# Use external Redis only
export REDIS_ENABLED=false
export REDIS_EXTERNAL_HOST=redis.example.com
export REDIS_EXTERNAL_PORT=6379

# Disable leader election (for single instance deployments)
export ENABLE_LEADER_ELECTION=false

# Disable webhook rate limiting (behind API gateway or load balancer)
export WEBHOOK_RATE_LIMIT_ENABLED=false
```

## Configuration Validation Rules

MANA validates the following constraints at startup:

### Redis Configuration
- `REDIS_PORT`: Must be between 1 and 65535
- `REDIS_SOCKET_CONNECT_TIMEOUT`: Must be positive
- `REDIS_SOCKET_TIMEOUT`: Must be positive
- `REDIS_MAX_CONNECTIONS`: Must be positive
- `REDIS_HEALTH_CHECK_INTERVAL`: Must be positive

### Retry and Rollback Configuration
- `MAX_RETRIES`: Must be ≥ 1
- `ROLLBACK_THRESHOLD`: Must be ≥ 1
- `INITIAL_BACKOFF`: Must be > 0
- `MAX_BACKOFF`: Must be > `INITIAL_BACKOFF`
- `BACKOFF_MULTIPLIER`: Must be > 1.0
- `JITTER_RANGE`: Must be between 0 and 1

### Health Check Configuration
- `HEALTH_CHECK_TIMEOUT`: Must be positive
- `HEALTH_CHECK_PORT`: Must be between 1 and 65535
- `MAX_APP_HEALTH_FAILURES`: Must be ≥ 1
- `HEALTH_CHECK_INTERVAL`: Must be positive
- `HEALTH_CHECK_TIMEOUT_SECONDS`: Must be positive

### GitHub Integration Configuration
- `GITHUB_REQUEST_TIMEOUT`: Must be positive
- `GITHUB_MAX_RETRIES`: Must be ≥ 0
- `GITHUB_RETRY_BACKOFF_FACTOR`: Must be ≥ 0

### Orchestrator Service Configuration
- `PORT`: Must be between 1 and 65535
- `WORKERS`: Must be ≥ 1
- `LOG_LEVEL`: Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL
- `AUDIT_LOG_RETENTION_DAYS`: Must be ≥ 1
- `METRICS_PORT`: Must be between 1 and 65535

### Resource Configuration
- All memory values must be valid Kubernetes quantity strings (e.g., 256Mi, 1Gi)
- All CPU values must be valid Kubernetes quantity strings (e.g., 100m, 500m)

### Advanced Configuration
- `STATE_TTL_SECONDS`: Must be positive
- `CLEANUP_OLD_STATES_INTERVAL`: Must be positive
- `MAX_CONCURRENT_HEALTH_CHECKS`: Must be ≥ 1
- `HEALTH_CHECK_TIMEOUT_SECONDS`: Must be positive
- `LEADER_ELECTION_LEASE_DURATION`: Must be positive
- `LEADER_ELECTION_RENEW_DEADLINE`: Must be positive and less than lease duration
- `LEADER_ELECTION_RETRY_PERIOD`: Must be positive
- `WEBHOOK_RATE_LIMIT_REQUESTS`: Must be positive
- `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS`: Must be positive

If any validation fails, MANA will log a detailed error message indicating which configuration value is invalid and why, then exit during startup.

## Upgrading Configuration

When upgrading MANA, you may need to update your configuration:

### Backward Compatibility
MANA strives to maintain backward compatibility for configuration options. However, occasionally:
- Deprecated options are removed (with advance notice in release notes)
- Default values change (rarely, and only for good reason)
- Validation rules become stricter
- New required options are added

### Checking for Breaking Changes
Before upgrading, always:
1. Read the [release notes](about/release-notes.md) for the target version
2. Check for any "Breaking Changes" or "Configuration Changes" sections
3. Test the upgrade in a non-production environment first

### Migration Strategies
When breaking changes are introduced:
1. **Deprecation Period**: Options are typically deprecated for one release before removal
2. **Automatic Migration**: Some changes are handled automatically by the application
3. **Manual Intervention**: Some changes require updating your configuration files
4. **Documentation**: Clear migration paths are provided in the release notes

## Troubleshooting Configuration Issues

### Common Configuration Problems

#### Invalid Environment Variable Format
**Symptom**: MANA fails to start with error about invalid value
**Solution**: Check that the environment variable is set correctly and matches the expected format
```bash
# Example error: "max_backoff must be > initial_backoff"
# Solution: Ensure MAX_BACKOFF > INITIAL_BACKOFF
export MAX_BACKOFF=300.0
export INITIAL_BACKOFF=10.0
```

#### Missing Required Variables
**Symptom**: MANA fails to start with error about missing required variable
**Solution**: Set all required environment variables
```bash
# Example error: "REDIS_HOST is required"
# Solution: Set the REDIS_HOST variable
export REDIS_HOST=redis-service
```

#### Invalid Kubernetes Resource Values
**Symptom**: Pod fails to start with OOMKilled or CrashLoopBackOff
**Solution**: Verify that resource requests and limits are valid Kubernetes quantities
```bash
# Invalid: "512" (missing unit)
# Valid: "512Mi" or "0.5Gi"
export RESOURCE_REQUESTS_MEMORY=512Mi
# Invalid: "1.5" (invalid CPU format)
# Valid: "1500m" or "1.5"
export RESOURCE_REQUESTS_CPU=500m
```

#### Port Conflicts
**Symptom**: Service fails to start or can't be accessed
**Solution**: Ensure the configured port is not already in use
```bash
# Check what's using port 8000
kubectl -n mana get pods -o wide
netstat -tulpn | grep 8000
# Change PORT if necessary
export PORT=8080
```

#### Redis Connection Issues
**Symptom**: Orchestrator crashes or reports Redis connection errors
**Solution**: Verify Redis connection details and network accessibility
```bash
# Test Redis connection
redis-cli -h $REDIS_HOST -p $REDIS_PORT ping
# Should return: PONG

# Check if password is required
if [ -n "$REDIS_PASSWORD" ]; then
  redis-cli -h $REDIS_HOST -p $REDIS_PORT -a $REDIS_PASSWORD ping
fi

# Check network connectivity
telnet $REDIS_HOST $REDIS_PORT
# Should connect successfully
```

#### GitHub Authentication Failures
**Symptom**: Retry functionality doesn't work or API calls fail
**Solution**: Verify GitHub token has correct permissions
```bash
# Test GitHub token (replace with actual values)
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GITHUB_REPO
# Should return repository information (not 401 or 403)

# Check token scopes (requires GitHub API v3)
# Token needs at least: repo, workflow
```

#### Health Check Configuration Issues
**Symptom**: Deployments are incorrectly marked as healthy/unhealthy
**Solution**: Verify health check configuration matches your application's actual health endpoint
```bash
# Test your application's health endpoint directly
curl -v http://<app-ip>:<app-port>/health
# Should return 200 with expected JSON

# Check that MANA's expected response matches what your app returns
export HEALTH_CHECK_EXPECTED_RESPONSE='{"status":"healthy"}'
# If your app returns {"status":"OK"}, adjust accordingly
export HEALTH_CHECK_EXPECTED_RESPONSE='{"status":"OK"}'

# Check headers if required
export HEALTH_CHECK_HEADERS='{"Authorization":"Bearer $TOKEN"}'
```

## Configuration Examples for Common Scenarios

### High Availability Setup
```bash
# Multiple orchestrator replicas for HA
export REPLICA_COUNT=3

# External Redis cluster for HA
export REDIS_ENABLED=false
export REDIS_EXTERNAL_HOST=redis-cluster.example.com
export REDIS_EXTERNAL_PORT=6379
export REDIS_EXTERNAL_PASSWORD=${REDIS_PASSWORD}
export REDIS_EXTERNAL_SSL=true

# Leader election to prevent conflicts
export ENABLE_LEADER_ELECTION=true
export LEADER_ELECTION_LEASE_DURATION=15
export LEADER_ELECTION_RENEW_DEADLINE=10
export LEADER_ELECTION_RETRY_PERIOD=2

# Autoscaling to handle load spikes
export AUTOSCALING_ENABLED=true
export AUTOSCALING_MIN_REPLICAS=3
export AUTOSCALING_MAX_REPLICAS=10
export AUTOSCALING_TARGET_CPU_UTILIZATION=70

# Increased resources for handling load
export RESOURCE_REQUESTS_MEMORY=1024Mi
export RESOURCE_REQUESTS_CPU=500m
export RESOURCE_LIMITS_MEMORY=2Gi
export RESOURCE_LIMITS_CPU=1000m

# Extended audit log retention for compliance
export AUDIT_LOG_RETENTION_DAYS=365

# Secure metrics endpoint
export METRICS_PORT=9090
# (Would typically be behind auth proxy or network policy)

# Webhook security
export WEBHOOK_AUTH_ENABLED=true
export WEBHOOK_AUTH_TOKEN=${WEBHOOK_SECRET}
export WEBHOOK_RATE_LIMIT_ENABLED=true
export WEBHOOK_RATE_LIMIT_REQUESTS=100
export WEBHOOK_RATE_LIMIT_WINDOW_SECONDS=60
```

### Resource-Constrained Environment
```bash
# Minimal resource usage
export REPLICA_COUNT=1
export RESOURCE_REQUESTS_MEMORY=128Mi
export RESOURCE_REQUESTS_CPU=50m
export RESOURCE_LIMITS_MEMORY=256Mi
export RESOURCE_LIMITS_CPU=100m

# Reduced Redis usage
export REDIS_ENABLED=true
# (uses embedded Redis with minimal persistence)

# Lower retry limits to reduce load
export MAX_RETRIES=2
export ROLLBACK_THRESHOLD=1
export INITIAL_BACKOFF=5.0
export MAX_BACKOFF=120.0

# Reduced monitoring overhead
export METRICS_ENABLED=false
export LOG_LEVEL=WARNING

# Less frequent cleanup to reduce Redis load
export CLEANUP_OLD_STATES_INTERVAL=7200  # 2 hours

# Shorter state TTL
export STATE_TTL_SECONDS=43200  # 12 hours
```

### Compliance-Focused Setup
```bash
# Extended audit log retention
export AUDIT_LOG_RETENTION_DAYS=2555  # 7 years

# Enhanced audit logging
export ENABLE_AUDIT_LOGGING=true
# (consider sending audit logs to SIEM or external storage)

# Strict validation
export LOG_LEVEL=INFO  # Avoid DEBUG in production to reduce noise

# Secure communications
export REDIS_SSL=true
export REDIS_SSL_CERT_REQS=required
export WEBHOOK_AUTH_ENABLED=true
export ENABLE_CORS=false

# Detailed metrics for auditing
export METRICS_ENABLED=true
export METRICS_PORT=9090
# (typically behind authentication)

# Regular key rotation friendly
# (secrets should be rotated independently of MANA config)

# Immutable infrastructure approach
# (config baked into images, not changed at runtime)
```

### Development/Demo Setup
```bash
# Quick iteration and testing
export REPLICA_COUNT=1
export RESOURCE_REQUESTS_MEMORY=64Mi
export RESOURCE_REQUESTS_CPU=25m
export RESOURCE_LIMITS_MEMORY=128Mi
export RESOURCE_LIMITS_CPU=50m

# Fast retry cycles for testing
export MAX_RETRIES=3
export ROLLBACK_THRESHOLD=2
export INITIAL_BACKOFF=2.0
export MAX_BACKOFF=30.0
export BACKOFF_MULTIPLIER=2.0

# Verbose logging for debugging
export LOG_LEVEL=DEBUG
export ENABLE_AUDIT_LOGGING=true

# Disable unnecessary features for speed
export METRICS_ENABLED=false
export ENABLE_LEADER_ELECTION=false
export AUTOSCALING_ENABLED=false

# Short timeouts for quick feedback
export HEALTH_CHECK_TIMEOUT=2
export HEALTH_CHECK_INTERVAL=5
export STATE_TTL_SECONDS=3600  # 1 hour
export CLEANUP_OLD_STATES_INTERVAL=300  # 5 minutes

# No persistence needed for disposable environments
# (but MANA still requires Redis for core functionality)
```

## Next Steps

After configuring MANA:

1. [Deploy MANA](getting-started/installation.md) using your chosen method
2. [Integrate with your CI/CD pipeline](usage/cicd-integration.md)
3. [Monitor MANA's operation](usage/monitoring.md)
4. [Troubleshoot any issues](usage/troubleshooting.md)
5. [Contribute to the project](contributing/contributing.md) if you have improvements

Remember that configuration is an ongoing process - you'll likely need to adjust settings based on your operational experience and changing requirements.