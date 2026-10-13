# Basic Usage Guide

This guide explains how to use MANA for monitoring and self-healing of your Kubernetes deployments once it's installed and configured.

## Prerequisites

Before using MANA, ensure you have:
- MANA installed and running in your cluster
- Your applications deployed to Kubernetes with proper labeling and health endpoints
- MANA configured to monitor the namespaces containing your applications
- (Optional but recommended) CI/CD integration configured for automatic retries

## How MANA Works

MANA operates through a continuous monitoring and decision-making cycle:

1. **Monitoring**: MANA periodically checks the health of deployments in configured namespaces
2. **Detection**: When a deployment becomes unhealthy, MANA records the failure
3. **Decision**: Based on configuration, MANA decides whether to retry or rollback
4. **Action**: MANA executes the chosen action (trigger CI/CD re-run or rollback)
5. **Verification**: MANA verifies the success of the action
6. **Logging**: All actions are recorded in the audit trail
7. **Repetition**: The cycle continues

## Monitoring Deployments

### Automatic Monitoring
Once MANA is running, it automatically monitors:
- All deployments in namespaces matching the `NAMESPACE_LABEL_SELECTOR`
- All deployments matching the `DEPLOYMENT_LABEL_SELECTOR` within those namespaces
- Deployments that have the required labels for version tracking

### Manual Monitoring
You can also manually check the status of specific deployments through MANA's API:

```bash
# Check the status of a specific deployment
curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/deployments/<deployment-namespace>/<deployment-name>

# Example
curl http://mana-orchestrator.mana.svc.cluster.local:8000/deployments/production/web-app
```

This returns detailed information including:
- Current status (success, failed, in_progress, etc.)
- Version information
- Retry count
- Failure type (if applicable)
- Timestamps
- Associated metadata

### Listing Recent Deployments
To see all deployments MANA is currently tracking:

```bash
# Get recent deployments (default: last 10)
curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/deployments/recent

# Get recent deployments for a specific namespace
curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/deployments/recent?namespace=production

# Get a specific number of recent deployments
curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/deployments/recent?limit=20
```

## Understanding Deployment States

MANA tracks deployments through several possible states:

| Status | Description |
|--------|-------------|
| `PENDING` | Deployment has been detected but initial checks haven't completed |
| `IN_PROGRESS` | Deployment is actively being rolled out |
| `SUCCESS` | Deployment has completed successfully and passed health checks |
| `FAILED` | Deployment has failed and exceeded retry thresholds |
| `ROLLING_BACK` | Rollback process is currently in progress |
| `ROLLED_BACK` | Rollback has completed successfully |
| `RETRY_SCHEDULED` | A retry attempt has been scheduled but not yet executed |

## Working with Deployments

### Viewing Deployment Details
For detailed information about a specific deployment:

```bash
curl http://mana-orchestrator.mana.svc.cluster.local:8000/deployments/production/web-app
```

Sample response:
```json
{
  "deployment_id": "abc123-def456-ghi789",
  "namespace": "production",
  "app_name": "web-app",
  "version": "1.2.3",
  "status": "SUCCESS",
  "retry_count": 0,
  "failure_type": null,
  "timestamp": 1698765432.123,
  "metadata": {
    "workflow_run_id": "123456789",
    "commit": "a1b2c3d4e5f6",
    "ref": "refs/heads/main",
    "event_name": "push",
    "workflow": "CI",
    "actor": "octocat"
  }
}
```

### Viewing Deployment Metrics
To get detailed metrics about a deployment:

```bash
curl http://mana-orchestrator.mana.svc.cluster.local:8000/metrics/deployment/<namespace>/<deployment-name>
```

Sample response:
```json
{
  "replicas": 3,
  "ready_replicas": 3,
  "available_replicas": 3,
  "unavailable_replicas": 0,
  "conditions": [
    {
      "type": "Available",
      "status": "True",
      "reason": "MinimumReplicasAvailable",
      "message": "Deployment has minimum availability."
    },
    {
      "type": "Progressing",
      "status": "True",
      "reason": "NewReplicaSetAvailable",
      "message": "ReplicaSet \"web-app-5d8b9c7f4\" has successfully progressed."
    }
  ]
}
```

## Manual Operations

While MANA is designed to be automatic, you can perform manual operations when needed.

### Manual Health Check
To manually check the health of a deployment:

```bash
curl -X POST http://mana-orchestrator.mana.svc.cluster.local:8000/health-check \
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

### Manual Rollback
To manually initiate a rollback to a specific version:

```bash
curl -X POST http://mana-orchestrator.mana.svc.cluster.local:8000/rollback \
  -H "Content-Type: application/json" \
  -d '{
    "namespace": "production",
    "deployment_name": "web-app",
    "target_version": "1.2.2"
  }'
```

Sample response:
```json
{
  "status": "success",
  "deployment_id": "manual-rollback-def456-ghi789-jkl012",
  "message": "Rolled back to version 1.2.2"
}
```

### Forcing a Success State
In rare cases where you need to manually mark a deployment as successful (not recommended for regular use):

```bash
# This would require direct API access or database modification
# Not exposed through the standard API for safety reasons
```

## Understanding Health Checks

MANA performs two types of health checks:

### 1. Pod-Level Health Checks
These check whether Kubernetes reports the pods as ready:
- Checks `ready_replicas` equals `replicas` for the deployment
- Ensures all pods are in the `Running` state
- Verifies that pods have passed their own liveness and readiness probes

### 2. Application-Level Health Checks
These check whether the application itself is responding correctly:
- Makes HTTP requests to the configured health endpoint (`/health` by default)
- Expects a specific response (usually JSON with `{status: "healthy"}`)
- Can be customized with different paths, methods, headers, and expected responses
- Tracks consecutive failures to avoid flapping decisions

### Health Check Process
MANA's health check process works as follows:

1. Check if deployment's `ready_replicas` equals `replicas`
   - If not, wait and retry (resets application failure counter)
   - If yes, proceed to application health check
2. Make HTTP request to application's health endpoint
   - If response matches expected criteria, deployment is healthy
   - If response doesn't match, increment application failure counter
   - If application failure counter exceeds threshold, mark deployment as unhealthy
3. If deployment is healthy, return success
4. If deployment is unhealthy, return failure and trigger recovery process

## Working with Namespaces

MANA can monitor multiple namespaces simultaneously.

### Viewing Monitored Namespaces
To see which namespaces MANA is currently monitoring:

```bash
# This would require a specific endpoint - for now, you can check:
kubectl -n mana get configmap mana-config -o yaml
# Or check the environment variables of the orchestrator pod
kubectl -n mana exec deploy/mana-orchestrator -- env | grep NAMESPACE
```

### Changing Monitored Namespaces
To change which namespaces MANA monitors, you need to:
1. Update the `NAMESPACE_LABEL_SELECTOR` environment variable
2. Restart the orchestrator pods

Example:
```bash
# Change to monitor only production and staging namespaces
kubectl -n mana set env deploy/mana-orchestrator NAMESPACE_LABEL_SELECTOR="name in (production,staging)"
kubectl -n mana rollout restart deploy/mana-orchestrator
```

Or using label selectors:
```bash
# Monitor namespaces with specific labels
kubectl -n mana set env deploy/mana-orchestrator NAMESPACE_LABEL_SELECTOR="environment in (production,staging),team in (frontend,backend)"
kubectl -n mana rollout restart deploy/mana-orchestrator
```

## Understanding Retry and Backoff

MANA uses exponential backoff for retry attempts to avoid overwhelming failing systems.

### Backoff Calculation
The backoff delay is calculated as:
```
backoff = initial_backoff × (backoff_multiplier ^ (retry_attempt - 1))
```
With the result capped at `max_backoff`.

### Example Backoff Sequence
With `initial_backoff=10.0`, `max_backoff=300.0`, `backoff_multiplier=2.0`:
- Attempt 1: 10.0 seconds
- Attempt 2: 20.0 seconds
- Attempt 3: 40.0 seconds
- Attempt 4: 80.0 seconds
- Attempt 5: 160.0 seconds
- Attempt 6: 300.0 seconds (capped)
- Attempt 7+: 300.0 seconds (remains capped)

### With Jitter
If jitter is enabled, a random amount is added to prevent thundering herd problems:
```
final_backoff = backoff + random(0, backoff × jitter_range)
```

## Viewing Audit Logs

MANA's audit logs provide a comprehensive record of all actions.

### Accessing Audit Logs
Audit logs are available through standard Kubernetes logging:

```bash
# Get recent audit logs from all orchestrator pods
kubectl -n mana logs -l app=mana-orchestrator

# Get logs for a specific time range
kubectl -n mana logs -l app=mana-orchestrator --since=1h

# Follow logs in real-time
kubectl -n mana logs -f -l app=mana-orchestrator

# Get logs for a specific deployment
kubectl -n mana logs -l app=mana-orchestrator | grep "deployment_id:abc123"
```

### Audit Log Format
Each audit log entry is a JSON object with the following structure:
```json
{
  "timestamp": "2023-11-01T14:30:00Z",
  "service": "self-healing-orchestrator",
  "action": "failure_detected",
  "success": true,
  "deployment_id": "abc123-def456-ghi789",
  "namespace": "production",
  "deployment_name": "web-app",
  "version": "1.2.3",
  "details": {
    "failure_type": "health_check_failure",
    "retry_count": 2
  }
}
```

### Common Audit Actions
| Action | Description |
|--------|-------------|
| `deployment_received` | Webhook received from CI/CD system |
| `failure_detected` | Deployment failure detected |
| `retry_initiated` | Retry attempt scheduled |
| `rollback_initiated` | Rollback process started |
| `rollback_completed` | Rollback completed successfully |
| `rollback_failed` | Rollback process failed |
| `health_check_started` | Health check began |
| `health_check_passed` | Health check passed |
| `health_check_failed` | Health check failed |
| `state_saved` | Deployment state saved to Redis |
| `state_retrieved` | Deployment state retrieved from Redis |
| `manual_intervention_required` | Automatic recovery exhausted, manual help needed |

## Working with CI/CD Systems

If you've configured GitHub Integration, MANA can automatically trigger workflow re-runs.

### How GitHub Integration Works
1. When a deployment fails, MANA checks if retry attempts remain
2. If yes, it calculates the appropriate backoff delay
3. After the delay, MANA triggers a re-run of the specified GitHub Actions workflow
4. The re-run proceeds with the same inputs as the original run
5. MANA monitors the result and repeats the process if needed

### Required Webhook Data
For GitHub integration to work, your CI/CD webhooks must include:
- `workflow_run_id`: The ID of the GitHub Actions workflow run
- Optional but recommended: commit, ref, event_name, workflow, actor

Example webhook payload:
```json
{
  "deployment_id": "12345",
  "namespace": "production",
  "app_name": "web-app",
  "version": "1.2.3",
  "status": "failed",
  "failure_type": "deployment_failure",
  "metadata": {
    "workflow_run_id": "67890",
    "commit": "a1b2c3d4e5f6g7h8i9j0",
    "ref": "refs/heads/main",
    "event_name": "push",
    "workflow": "CI",
    "actor": "octocat"
  }
}
```

### Manual Retry Trigger
If you need to manually trigger a retry (equivalent to what GitHub integration does):

```bash
# This would require direct API access or triggering the CI/CD system manually
# Not exposed through the standard API for safety reasons
# Instead, trigger your CI/CD system directly and send a success webhook when done
```

## Best Practices for Usage

### 1. Monitor MANA Itself
Set up monitoring for the MANA orchestrator:
- Check the `/health` endpoint regularly
- Monitor pod restart rates
- Track resource usage (CPU, memory)
- Watch for crash loops or extended unavailability

### 2. Track Key Metrics
Monitor these key MANA metrics:
- `deployments_total{status="failed"}` - Failed deployments
- `rollbacks_total` - Number of rollbacks triggered
- `retries_total` - Number of retry attempts
- `deployment_duration_seconds` - How long deployments take
- `active_deployments` - How many deployments MANA is tracking
- `health_check_duration_seconds` - How long health checks take

### 3. Set Up Alerts
Consider alerting on:
- High failure rates (sudden increase in failed deployments)
- Frequent rollbacks (more than X rollbacks per hour)
- MANA orchestrator unavailability
- High retry counts for specific deployments
- Audit log entries requiring manual intervention

### 4. Regularly Review Audit Logs
Periodically review audit logs to:
- Identify recurring failure patterns
- Understand recovery effectiveness
- Spot potential configuration issues
- Ensure compliance with internal policies

### 5. Tune Based on Observations
Adjust configuration based on what you see:
- Increase `max_retries` for flaky but recoverable services
- Decrease `rollback_threshold` for critical services that should fail fast
- Adjust backoff values based on typical recovery times
- Modify health check thresholds based on application behavior
- Update resource requests/limits based on actual usage

### 6. Document Your Configuration
Keep track of:
- Why specific non-default values were chosen
- Any customizations or extensions made
- Known limitations or workarounds
- Procedures for common operational tasks

### 7. Test Failure Scenarios
Regularly test MANA's self-healing capabilities:
- Simulate different failure types
- Verify retry behavior works as expected
- Confirm rollback executes correctly
- Test GitHub integration (if configured)
- Validate alerting and notification systems

### 8. Keep MANA Updated
Regularly update MANA to:
- Get security patches
- Benefit from performance improvements
- Access new features
- Receive bug fixes
- Stay compatible with newer Kubernetes versions

## Troubleshooting Common Usage Issues

### Deployments Not Being Monitored
**Symptom**: MANA doesn't appear to be tracking your deployments
**Checks**:
- Verify namespace selectors match your deployment namespaces
- Check that deployments have the required labels for version tracking
- Ensure MANA is running in the expected namespace
- Confirm the orchestrator pods are healthy and running
- Check audit logs for any errors during startup

### Health Checks Always Failing
**Symptom**: MANA marks all deployments as unhealthy
**Checks**:
- Verify your application's health endpoint is accessible
- Check that the health endpoint returns the expected format
- Confirm health check configuration (port, path, expected response)
- Test the health endpoint manually from within the cluster
- Look for network policies blocking access to your pods

### Retries Not Happening
**Symptom**: Failed deployments aren't triggering retries
**Checks**:
- Verify GitHub token is configured and has correct permissions
- Check that webhooks include the required `workflow_run_id`
- Confirm `max_retries` is set to a value ≥ 1
- Ensure `rollback_threshold` is appropriately set
- Look for errors in the orchestrator logs related to GitHub API calls

### Rollbacks Not Working
**Symptom**: MANA attempts rollbacks but they don't succeed
**Checks**:
- Verify previous versions actually exist (check ReplicaSet history)
- Confirm the orchestrator has permission to update deployments
- Check that the target version is valid and accessible
- Ensure the rollout isn't being blocked by resource constraints
- Look for pod scheduling issues or image pull problems

### High Resource Usage
**Symptom**: MANA orchestrator is using too much CPU or memory
**Checks**:
- Review resource requests and limits
- Check if you're monitoring too many namespaces/deployments
- Look for memory leaks in long-running processes
- Consider reducing audit log verbosity if not needed for compliance
- Check if metrics collection is causing overhead

### Audit Logs Growing Too Large
**Symptom**: Audit logs are consuming excessive disk space
**Checks**:
- Verify `AUDIT_LOG_RETENTION_DAYS` is set appropriately
- Check if `CLEANUP_OLD_STATES_INTERVAL` is configured correctly
- Consider reducing log level from DEBUG to INFO or WARNING in production
- Evaluate whether all audit actions are necessary for your use case
- Look into external log aggregation and rotation solutions

## Next Steps

After becoming familiar with basic usage:

1. [Explore the API Reference](reference/api.md) for programmatic interaction
2. [Learn about Advanced Configuration](configuration/overview.md) for fine-tuning MANA
3. [Set up Monitoring and Alerting](usage/monitoring.md) for operational visibility
6. [Explore Contributing Guidelines](contributing/contributing.md) if you wish to improve MANA
7. [Review the Architecture](architecture/overview.md) to understand how MANA works internally

Remember that effective usage of MANA involves ongoing observation, tuning, and adaptation to your specific environment and requirements.