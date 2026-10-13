# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with MANA (Self-Healing CI/CD Pipeline).

## General Troubleshooting Approach

When encountering issues with MANA, follow this systematic approach:

1. **Identify the symptom**: What exactly is not working as expected?
2. **Gather information**: Collect logs, metrics, and configuration details
3. **Form a hypothesis**: What might be causing the issue?
4. **Test the hypothesis**: Verify or disprove your theory
5. **Implement a solution**: Fix the underlying cause
6. **Verify the fix**: Confirm the issue is resolved
7. **Prevent recurrence**: Consider how to avoid similar issues in the future

## Common Issue Categories

Issues with MANA typically fall into these categories:
- Installation and deployment problems
- Configuration issues
- Connectivity and network problems
- Permission and authorization issues
- Performance and resource problems
- Logic and behavior issues
- Monitoring and observability problems

## Installation and Deployment Issues

### Issue: Pods CrashLoopBackOff or Error

**Symptoms**:
- Pods repeatedly restarting
- `kubectl get pods` shows `CrashLoopBackOff` or `Error` status
- Logs show repeated startup attempts followed by crashes

**Possible Causes**:
- Missing or incorrect environment variables
- Invalid configuration values
- Missing dependencies (Redis not available)
- Insufficient permissions or RBAC issues
- Container image problems
- Resource constraints (OOMKilled)

**Diagnosis Steps**:
1. Check pod logs: `kubectl -n mana logs <pod-name>`
2. Describe the pod: `kubectl -n mana describe pod <pod-name>`
3. Check events: `kubectl -n mana get events --sort-by='.metadata.creationTimestamp'`
4. Verify configuration: Check environment variables and config maps/secrets
5. Test dependencies: Can the pod reach Redis and Kubernetes API?

**Solutions**:
- Fix missing/incorrect environment variables
- Correct invalid configuration values
- Ensure dependencies are running and accessible
- Fix RBAC permissions if needed
- Check container image availability and correctness
- Adjust resource requests/limits if OOMKilled

### Issue: Pods Pending or ContainerCreating

**Symptoms**:
- Pods stuck in `Pending` or `ContainerCreating` state
- Not progressing to `Running`

**Possible Causes**:
- Insufficient cluster resources (CPU, memory, storage)
- Image pull problems (private registry, auth issues)
- Node selector or affinity issues
- Tolerations and taints mismatches
- Volume binding failures
- Scheduler issues

**Diagnosis Steps**:
1. Describe the pod: `kubectl -n mana describe pod <pod-name>`
2. Check events for scheduling issues
3. Check node status and resource availability
4. Verify image pull secrets if using private registry
5. Check storage class and PVC availability

**Solutions**:
- Free up cluster resources or add nodes
- Fix image pull secrets or registry accessibility
- Adjust node selectors, affinities, taints, or tolerations
- Fix storage provisioning issues
- Wait for scheduler to resolve transient issues

### Issue: Service Not Accessible

**Symptoms**:
- Can't access MANA API via service
- Port forwarding works but service doesn't
- Connection refused or timeout when accessing service

**Possible Causes**:
- Service not properly defined
- Service selector not matching pod labels
- Network policies blocking access
- Service type issues (ClusterIP vs NodePort vs LoadBalancer)
- External load balancer or ingress problems

**Diagnosis Steps**:
1. Check service definition: `kubectl -n mana get svc <service-name>`
2. Check endpoints: `kubectl -n mana get endpoints <service-name>`
3. Check pod labels match service selector: `kubectl -n mana get pods -l <selector>`
4. Test port forwarding: `kubectl -n mana port-forward svc/<service-name> <local-port>:<target-port>`
5. Check network policies: `kubectl -n mana get networkpolicies`
6. Check ingress if applicable: `kubectl -n mana get ingress`

**Solutions**:
- Fix service selector to match pod labels
- Ensure service type matches your access method
- Adjust network policies to allow necessary traffic
- Fix ingress configuration if using
- Check cloud provider load balancer status

## Configuration Issues

### Issue: Invalid Configuration Values

**Symptoms**:
- MANA fails to start with validation errors
- Logs show "Invalid configuration value" messages
- Container exits immediately after start

**Possible Causes**:
- Environment variables set to invalid values
- Configuration values outside allowed ranges
- Type mismatches (string vs number)
- Missing required values
- Invalid JSON in complex configuration values

**Diagnosis Steps**:
1. Check startup logs for specific validation errors
2. Review all environment variables and their values
3. Validate each configuration parameter against allowed ranges
4. Check JSON formatting for complex values
5. Verify required variables are set

**Solutions**:
- Correct invalid values to be within allowed ranges
- Set missing required environment variables
- Fix type mismatches (ensure numbers are numbers, booleans are booleans)
- Fix JSON formatting in complex values
- Use configuration validation tools or scripts before deployment

### Issue: Configuration Not Taking Effect

**Symptoms**:
- Changes to configuration don't seem to affect behavior
- MANA appears to be using old or default values
- Restarting pods doesn't change behavior

**Possible Causes**:
- Configuration not actually being applied (wrong env var names)
- Configuration being overridden by other sources
- Caching or state persistence preventing changes from taking effect
- Restart not actually happening (image not changed, etc.)

**Diagnosis Steps**:
1. Verify environment variables are set correctly on the pod
   ```bash
   kubectl -n mana exec <pod-name> -- env | grep <VARIABLE>
   ```
2. Check if config maps/secrets are correctly mounted
3. Verify annotation or label changes are triggering pod restarts
4. Check if state persistence is preventing behavior changes
5. Look for multiple configuration sources overriding each other

**Solutions**:
- Ensure environment variable names match exactly what MANA expects
- Fix config map/secret mounting if not working correctly
- Annotate or label resources to force pod recreation when needed
- Consider clearing state if needed (with caution)
- Establish a single source of truth for configuration

### Issue: Configuration Drift

**Symptoms**:
- Behavior changes over time without intentional changes
- Different instances behaving differently
- Configuration appears to reset or change unexpectedly

**Possible Causes**:
- Multiple sources of truth for configuration
- Automatic remediation systems overriding settings
- Human error in manual updates
- Configuration reloading from external sources
- Secrets rotation or updates causing changes

**Diagnosis Steps**:
1. Identify all sources of configuration (env vars, config maps, secrets, files, etc.)
2. Check for automated processes that might modify configuration
3. Review audit trails for configuration changes
4. Check for secrets rotation schedules
5. Look for configuration drift detection tools

**Solutions**:
- Establish a single source of truth for configuration
- Implement configuration management automation
- Use infrastructure as code (IaC) for consistent deployment
- Implement change approval workflows
- Use configuration drift detection and correction tools
- Schedule regular configuration audits

## Connectivity and Network Problems

### Issue: Cannot Connect to Redis

**Symptoms**:
- MANA fails to start with Redis connection errors
- Logs show "Redis connection failed" or similar
- Health checks show Redis as unhealthy
- State persistence issues

**Possible Causes**:
- Incorrect Redis host or port
- Network connectivity issues
- Redis service not running
- Authentication problems (wrong password, missing auth)
- TLS/SSL configuration issues
- Resource exhaustion on Redis
- Network policies blocking access

**Diagnosis Steps**:
1. Test Redis connectivity from within the cluster:
   ```bash
   # Run a temporary pod to test connectivity
   kubectl run -i --tty --rm debug --image=redis:alpine --restart=Never \
     -- redis-cli -h <redis-host> -p <redis-port> ping
   ```
2. Test with password if needed:
   ```bash
   kubectl run -i --tty --rm debug --image=redis:alpine --restart=Never \
     -- redis-cli -h <redis-host> -p <redis-port> -a <password> ping
   ```
3. Check Redis service status:
   ```bash
   kubectl get svc -n <redis-namespace>
   kubectl get pods -n <redis-namespace> -l app=redis
   ```
4. Check network policies:
   ```bash
   kubectl get networkpolicies --all-namespaces
   ```
5. Check Redis logs for connection attempts or errors

**Solutions**:
- Fix incorrect Redis host or port configuration
- Resolve network connectivity issues
- Start or fix Redis service if not running
- Correct Redis authentication credentials
- Fix TLS/SSL configuration if needed
- Address Redis resource exhaustion (scale up, optimize usage)
- Adjust network policies to allow necessary traffic
- Consider using Redis connection pooling if applicable

### Issue: Cannot Connect to Kubernetes API

**Symptoms**:
- MANA fails to start with Kubernetes connection errors
- Logs show "Kubernetes config failed" or similar
- Health checks show Kubernetes as unhealthy
- Unable to monitor or modify deployments

**Possible Causes**:
- Incorrect Kubernetes configuration
- Missing or incorrect kubeconfig or in-cluster config
- Network connectivity issues to API server
- Authentication or authorization problems
- API server not reachable or down
- RBAC permissions insufficient

**Diagnosis Steps**:
1. Test Kubernetes API connectivity from within the cluster:
   ```bash
   kubectl get ns
   # Should list namespaces without error
   ```
2. Check if using in-cluster config or kubeconfig:
   - In-cluster: Should work when running inside cluster
   - Kubeconfig: Need proper config mounted
3. Check API server status:
   ```bash
   kubectl cluster-info
   kubectl get nodes
   ```
4. Check network policies affecting API server access
5. Verify service account and RBAC permissions
6. Check API server logs for authentication errors

**Solutions**:
- Fix Kubernetes configuration (in-cluster vs kubeconfig)
- Resolve network connectivity issues to API server
- Correct authentication (service account token, client certs)
- Address API server availability issues
- Fix RBAC permissions to grant necessary access
- Resolve network policies blocking API access
- Consider using a proxy or API gateway if needed

### Issue: Cannot Reach Application Health Endpoints

**Symptoms**:
- MANA marks deployments as unhealthy despite pods being ready
- Health check failures in logs or metrics
- Applications appear to be working normally when accessed directly
- Inconsistent health check results

**Possible Causes**:
- Network policies blocking access to application pods
- Service issues (not exposing correct ports)
- DNS resolution problems within cluster
- Application not actually listening on expected port/path
- Firewall or security group issues
- Istio/service mesh interfering with traffic
- Incorrect health check configuration

**Diagnosis Steps**:
1. Test health endpoint from within the cluster:
   ```bash
   # Run a temporary pod to test connectivity
   kubectl run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never \
     -- curl -v http://<app-pod-ip>:<app-port>/<health-path>
   ```
2. Check service configuration:
   ```bash
   kubectl -n <namespace> get svc <app-service>
   kubectl -n <namespace> get endpoints <app-service>
   ```
3. Verify application is listening on expected port:
   ```bash
   kubectl -n <namespace> exec <app-pod> -- netstat -tulpn
   # or
   kubectl -n <namespace> exec <app-pod> -- ss -tulpn
   ```
4. Check network policies:
   ```bash
   kubectl get networkpolicies -n <namespace>
   ```
5. Verify health check configuration matches actual endpoint
6. Check for service mesh interference (Istio, Linkerd, etc.)
7. Test DNS resolution:
   ```bash
   kubectl run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never \
     -- nslookup <app-service>.<namespace>.svc.cluster.local
   ```

**Solutions**:
- Fix network policies to allow necessary traffic
- Correct service configuration to expose proper ports
- Fix DNS issues if present
- Ensure application is actually listening on expected port/path
- Correct health check configuration to match actual endpoint
- Address firewall or security group issues
- Configure service mesh to allow necessary traffic if applicable
- Consider using headless services or direct pod access if needed

## Permission and Authorization Issues

### Issue: Insufficient Kubernetes Permissions

**Symptoms**:
- MANA can't read deployments or replica sets
- MANA can't update deployments for rollback
- MANA can't create events
- Logs show authorization errors or forbidden responses

**Possible Causes**:
- Incorrect RBAC roles or bindings
- Missing permissions for required resources
- Using wrong service account
- Namespace restrictions
- API version mismatches

**Diagnosis Steps**:
1. Check the service account being used:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep serviceAccountName
   ```
2. Check the role bindings for that service account:
   ```bash
   kubectl -n mana get rolebindings
   kubectl -n mana get clusterrolebindings
   ```
3. Check what permissions the role actually has:
   ```bash
   kubectl -n mana get role <role-name> -o yaml
   # or
   kubectl get clusterrole <clusterrole-name> -o yaml
   ```
4. Test access directly:
   ```bash
   # Impersonate the service account to test permissions
   kubectl auth can-i <verb> <resource> --namespace <namespace> --as=system:serviceaccount:<namespace>:<service-account>
   ```
5. Check API versions being used vs available

**Solutions**:
- Fix RBAC roles to include necessary permissions
- Create or correct role bindings
- Ensure correct service account is being used
- Fix namespace restrictions if applicable
- Update API versions to match what's available in the cluster
- Consider using aggregated APIs or extension APIs if needed
- Use `kubectl auth can-i` to test specific permissions

### Issue: GitHub API Permission Problems

**Symptoms**:
- Retry functionality not working
- Logs show GitHub API authentication or authorization errors
- 401 or 403 responses from GitHub API
- Unable to trigger workflow re-runs

**Possible Causes**:
- Incorrect or missing GitHub token
- Token lacks required scopes or permissions
- Token expired or revoked
- Repository access restrictions
- Organization or enterprise policies blocking access
- Webhook signature validation failing (if configured)

**Diagnosis Steps**:
1. Verify GitHub token is set correctly:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep GITHUB_TOKEN
   # Or check the secret if using one
   ```
2. Test the GitHub token directly:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/user
   # Should return your user information
   ```
3. Check token scopes:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/user/repos
   # Should list repositories you have access to
   ```
4. Test access to the specific repository:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/repos/$GITHUB_REPO
   # Should return repository information
   ```
5. Check workflow permissions:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/repos/$GITHUB_REPO/actions/runs
   # Should list workflow runs (requires workflow scope)
   ```
6. Check if using GitHub Enterprise and need different API base URL
7. Verify webhook signature validation if configured

**Solutions**:
- Fix or refresh GitHub token
- Ensure token has required scopes (at minimum: `repo`, `workflow`)
- Grant repository access if needed
- Check organization policies and request exceptions if needed
- Use correct API base URL for GitHub Enterprise
- Fix webhook signature validation if configured
- Consider using a GitHub App instead of personal token for better permissions control
- Check if token needs to be re-authorized after org policy changes

## Performance and Resource Problems

### Issue: High Memory Usage

**Symptoms**:
- OOMKilled events
- High memory usage in monitoring
- Container restarts due to memory limits
- System slowing down or becoming unresponsive

**Possible Causes**:
- Memory leaks in the application
- Too many deployment states retained in Redis
- Excessive audit log generation
- Large objects being stored in Redis
- Inefficient data structures or algorithms
- Memory fragmentation
- Resource requests/limits set too low

**Diagnosis Steps**:
1. Check memory usage over time:
   ```bash
   kubectl -n mana top pods
   # or
   kubectl -n mana describe pod <pod-name> | grep -A 10 "Containers:"
   ```
2. Monitor memory metrics:
   ```bash
   # Assuming Prometheus is set up
   mana_orchestrator_memory_usage_bytes
   mana_orchestrator_memory_working_set_bytes
   ```
3. Check for memory leaks:
   - Look for steadily increasing memory usage over time
   - Check if memory usage resets on restart
   - Monitor specific memory pools if available
4. Check Redis memory usage if using external Redis:
   ```bash
   kubectl -n <redis-namespace> top pods
   # or redis-cli info memory
   ```
4. Check audit log generation rate:
   ```bash
   mana_audit_log_entries_total
   ```
5. Check number of tracked deployments:
   ```bash
   mana_active_deployments
   ```
6. Analyze heap dumps if possible (advanced)
7. Check for memory leaks in dependencies

**Solutions**:
- Fix memory leaks in the codebase
- Adjust state TTL to reduce Redis memory usage:
  ```bash
  export STATE_TTL_SECONDS=86400  # 24 hours (default)
  # Consider reducing for high-volume environments
  export STATE_TTL_SECONDS=43200  # 12 hours
  ```
- Reduce audit log verbosity if not needed for compliance:
  ```bash
  export LOG_LEVEL=INFO  # Instead of DEBUG
  ```
- Optimize data structures and algorithms
- Increase memory limits if consistently hitting them (after verifying no leaks)
- Fix resource requests/limits to match actual usage
- Consider using Redis eviction policies if appropriate
- Implement log rotation or external log aggregation
- Consider partitioning state if tracking very large numbers of deployments

### Issue: High CPU Usage

**Symptoms**:
- High CPU usage in monitoring
- CPU throttling (if limits set)
- Increased latency
- Poor responsiveness
- High system load

**Possible Causes**:
- Inefficient algorithms or data structures
- Excessive polling or looping
- Too many concurrent operations
- Inefficient use of external services (Redis, Kubernetes API, GitHub)
- Lock contention or threading issues
- Resource requests/limits set too low
- Unexpected workload spikes

**Diagnosis Steps**:
1. Check CPU usage over time:
   ```bash
   kubectl -n mana top pods
   # or
   kubectl -n mana describe pod <pod-name> | grep -A 10 "Containers:"
   ```
2. Monitor CPU metrics:
   ```bash
   mana_orchestrator_cpu_usage_seconds_total
   # or rate-based: rate(mana_orchestrator_cpu_usage_seconds_total[5m])
   ```
3. Look for spikes in specific operations:
   - Health check rate: `mana_health_checks_total`
   - Webhook request rate: `mana_webhook_requests_total`
   - Audit log generation: `mana_audit_log_entries_total`
   - Retry processing: `mana_retries_total`
   - Rollback operations: `mana_rollbacks_total`
4. Check for lock contention or threading issues (advanced)
5. Profile CPU usage if possible (perf, py-spy, etc.)
6. Check for inefficient loops or algorithms in code
7. Verify resource requests/limits aren't artificially constraining CPU

**Solutions**:
- Optimize inefficient algorithms or data structures
- Reduce polling frequency where possible (increase intervals)
- Limit concurrent operations if causing contention
- Optimize external service interactions (batch requests, better caching)
- Fix lock contention or threading issues
- Adjust resource requests/limits based on actual usage
- Implement caching for frequently accessed data
- Consider asynchronous processing for I/O-bound operations
- Optimize regex or string operations if applicable
- Look for accidental quadratic or worse complexity

### Issue: High Latency or Poor Responsiveness

**Symptoms**:
- Slow API response times
- Delayed health checks
- Slow retry or rollback execution
- Poor user experience when interacting with MANA
- Increased metrics for operation durations

**Possible Causes**:
- Network latency to dependencies (Redis, Kubernetes API, GitHub)
- Inefficient algorithms or data structures
- Lock contention or blocking operations
- Resource starvation (CPU, memory, I/O)
- External service latency or downtime
- Improper use of synchronous vs asynchronous operations
- Queue buildup or backpressure
- Garbage collection pauses
- Resource throttling

**Diagnosis Steps**:
1. Check latency metrics:
   ```bash
   mana_deployment_duration_seconds
   mana_health_check_duration_seconds
   mana_webhook_request_duration_seconds
   ```
2. Check dependency latency:
   - Redis: `redis_latency_seconds` (if available)
   - Kubernetes API: measure API call durations
   - GitHub: measure API call durations
3. Check for blocking operations in logs or traces
4. Check resource utilization during latency spikes
5. Look for correlation with specific operations or workloads
6. Check garbage collection timing and duration
7. Look for queue depths or backpressure indicators
8. Check network latency between components

**Solutions**:
- Fix network latency issues (move closer geographically, better routing)
- Optimize inefficient algorithms or data structures
- Reduce lock contention or use better locking strategies
- Ensure adequate resources to prevent starvation
- Address external service latency (contact providers, use regional instances)
- Use asynchronous operations where appropriate for I/O-bound tasks
- Implement caching to reduce repeated work
- Consider backpressure handling and queue limits
- Tune garbage collection if using a language with configurable GC
- Look for and eliminate unnecessary work in critical paths
- Consider implementing circuit breakers for external dependencies
- Use bulk or batch operations where applicable

## Logic and Behavior Issues

### Issue: Retries Not Happening

**Symptoms**:
- Failed deployments not triggering retries
- No retry attempts visible in logs or metrics
- Deployments going straight to rollback or failure state
- Retry counter staying at zero

**Possible Causes**:
- `max_retries` set to 0
- `rollback_threshold` set to 0 or 1
- GitHub integration not configured (missing token or repo)
- Webhooks missing required `workflow_run_id`
- Failure type not recognized or mapped correctly
- State management issues preventing retry tracking
- Conditions preventing retry logic from executing

**Diagnosis Steps**:
1. Check `max_retries` configuration:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep MAX_RETRIES
   ```
2. Check `rollback_threshold` configuration:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep ROLLBACK_THRESHOLD
   ```
3. Verify GitHub integration is configured:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep GITHUB_TOKEN
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep GITHUB_REPO
   ```
4. Check webhook payloads for `workflow_run_id`:
   - Look at audit logs for received webhooks
   - Check if `workflow_run_id` is present in metadata
5. Check failure type mapping in code/logs
6. Look for state management errors in logs
7. Check for any conditions that might skip retry logic
8. Verify retry logic is actually being executed (add logging if needed)

**Solutions**:
- Set `max_retries` to appropriate value (≥ 1)
- Set `rollback_threshold` appropriately (typically 1-3)
- Configure GitHub integration with valid token and repo
- Ensure webhooks include required `workflow_run_id`
- Fix failure type mapping if incorrect
- Resolve state management issues
- Remove or fix conditions preventing retry logic
- Consider adding more detailed logging to retry logic for debugging

### Issue: Rollbacks Not Happening

**Symptoms**:
- Failed deployments not triggering rollbacks after retries exhausted
- Deployments staying in failed state indefinitely
- No rollback attempts visible in logs or metrics
- Retry count reaching max but no subsequent action

**Possible Causes**:
- `rollback_threshold` set too high (greater than `max_retries`)
- No previous version available for rollback
- State management issues preventing version retrieval
- Permission issues preventing deployment updates
- Conditions preventing rollback logic from executing
- Issues with the rollback execution process itself

**Diagnosis Steps**:
1. Check `rollback_threshold` vs `max_retries`:
   ```bash
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep MAX_RETRIES
   kubectl -n mana get deploy mana-orchestrator -o yaml | grep ROLLBACK_THRESHOLD
   ```
2. Check if previous version is available:
   - Look at audit logs for successful deployments
   - Check if `save_successful_deployment_version` is being called
   - Verify version tracking is working
3. Check state management for version retrieval:
   - Look for errors in `get_previous_version` function
   - Verify Redis is storing successful versions correctly
4. Check permissions for deployment updates:
   - Verify RBAC includes update permissions for deployments
   - Check if any admission controllers are blocking updates
5. Look for conditions preventing rollback logic from executing
6. Check rollback execution process for errors:
   - Look for errors in `rollback_deployment` function
   - Verify Kubernetes API calls are working
   - Check if pod scheduling or resource issues are preventing rollout

**Solutions**:
- Ensure `rollback_threshold` ≤ `max_retries` (typically `rollback_threshold` = `max_retries` - 1)
- Fix version tracking and storage mechanisms
- Resolve state management issues
- Fix RBAC permissions to allow deployment updates
- Remove or fix conditions preventing rollback logic
- Fix rollback execution process errors
- Address pod scheduling or resource issues if preventing rollout
- Consider improving version tracking mechanism if needed

### Issue: Health Checks Always Failing

**Symptoms**:
- All deployments marked as unhealthy
- Health check failures in logs or metrics
- Applications working fine when accessed directly
- Consistent failure across all deployments

**Possible Causes**:
- Health check configuration doesn't match actual endpoints
- Network issues preventing access to application pods
- Application health endpoints not implemented or incorrect
- DNS or service discovery issues
- Service mesh or proxy interfering with traffic
- Firewall or security group blocking access
- Incorrect health check configuration (path, port, method, expected response)
- Health check timeout too low
- Concurrent health check limit too low causing queueing

**Diagnosis Steps**:
1. Test health endpoint from within cluster:
   ```bash
   kubectl run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never \
     -- curl -v http://<app-pod-ip>:<app-port>/<health-path>
   ```
2. Verify health check configuration matches actual endpoint:
   - Path
   - Port
   - Method
   - Headers (if any)
   - Expected response format
3. Check network connectivity:
   - Can you reach the pod IP from within the cluster?
   - Are network policies allowing the traffic?
   - Is DNS resolving correctly?
4. Check if service mesh is interfering:
   - Istio: Check destination rules, virtual services
   - Linkerd: Check service profiles
   - Linkerd: Check if proxy is injecting and causing issues
5. Check firewall or security group rules
6. Verify health check timeout is reasonable
7. Check concurrent health check limit
8. Look for health check errors in logs for specific details

**Solutions**:
- Fix health check configuration to match actual endpoint
- Resolve network connectivity issues
- Fix or implement proper health endpoints in applications
- Fix DNS or service discovery issues
- Adjust service mesh configuration to allow necessary traffic
- Fix firewall or security group rules
- Correct health check configuration (path, port, method, expected response)
- Increase health check timeout if too low
- Increase concurrent health check limit if too low
- Fix any issues causing health check errors

### Issue: Rollbacks Not Succeeding

**Symptoms**:
- MANA attempts rollback but deployment doesn't recover
- Rollback appears to start but doesn't complete
- Deployment stays in unhealthy state after rollback attempt
- Rollback metrics show failures
- Application still unhealthy after rollback

**Possible Causes**:
- Previous version actually unhealthy or broken
- Issues with the rollout process (resource constraints, scheduling)
- Problems with the new version that aren't immediately apparent
- Kubernetes issues preventing proper rollout
- Container image issues (wrong tag, inaccessible registry)
- Configuration or environment differences between versions
- Health check not actually verifying application correctness
- Race conditions or timing issues in rollback verification

**Diagnosis Steps**:
1. Verify the previous version was actually healthy:
   - Check audit logs for when it was marked as successful
   - Check if it had successful health checks after deployment
   - Verify it was running without issues before being replaced
2. Check the rollout process:
   - Monitor pod creation and termination during rollback
   - Check for resource constraints (OOMKilled, CrashLoopBackOff)
   - Look for scheduling issues (unschedulable pods)
   - Check image pull status
3. Verify the container image is correct and accessible:
   ```bash
   # Check if you can pull the image from within the cluster
   kubectl run -i --tty --rm debug --image=<image>:<tag> --restart=Never -- sleep 10
   ```
4. Check for configuration or environment differences:
   - Compare config maps, secrets, environment variables between versions
   - Check for any changes that might affect behavior
5. Verify health check is actually working correctly:
   - Test the health endpoint on the rolled-back version directly
   - Check if health check is verifying the right thing
6. Look for race conditions in rollback verification:
   - Is there enough time for the application to start up?
   - Is the health check happening too early?
   - Are we checking the right pods or the right set of pods?
7. Check for Kubernetes issues preventing proper rollout:
   - Node affinity/anti-affinity issues
   - Taint/toleration problems
   - Volume binding issues
   - Quota limits

**Solutions**:
- Only rollback to versions that were actually verified as healthy
- Fix rollout process issues (resources, scheduling, image access)
- Address any problems with the version being rolled back to
- Fix Kubernetes issues preventing proper rollout
- Ensure configuration and environment are appropriate for the version
- Improve health check to actually verify application correctness
- Adjust rollback verification timing to allow for startup
- Fix race conditions in rollback logic
- Address any Kubernetes-specific issues preventing proper rollout

## Monitoring and Observability Issues

### Issue: No Metrics in Prometheus

**Symptoms**:
- No MANA metrics appearing in Prometheus
- Graphs showing no data
- Targets showing as down in Prometheus
- Unable to query MANA metrics

**Possible Causes**:
- MANA not exposing `/metrics` endpoint
- ServiceMonitor or PodMonitor misconfigured
- Network policies blocking Prometheus access
- MANA service not reachable from Prometheus
- Prometheus not scraping correctly
- Metrics being filtered or transformed incorrectly
- MANA not actually generating metrics

**Diagnosis Steps**:
1. Test if `/metrics` endpoint is accessible:
   ```bash
   # From within the cluster
   curl http://mana-orchestrator.<namespace>.svc.cluster.local:8000/metrics
   # Should return metrics text

   # From outside the cluster (if applicable)
   curl http://<external-ip>:<node-port>/metrics
   ```
2. Check ServiceMonitor or PodMonitor configuration:
   ```bash
   kubectl get servicemonitor -n <monitoring-namespace>
   kubectl get podmonitor -n <monitoring-namespace>
   ```
3. Check network policies:
   ```bash
   kubectl get networkpolicies -n <namespace>
   ```
4. Test connectivity from Prometheus to mana service:
   - From a pod in the Prometheus namespace:
     ```bash
     kubectl run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never \
       -- curl -v http://mana-orchestrator.mana.svc.cluster.local:8000/metrics
     ```
5. Check Prometheus targets:
   ```bash
   # In Prometheus UI: Status -> Targets
   # Look for mana target and see why it's down
   ```
6. Check if MANA is actually generating metrics:
   - Look at mana logs for metric generation errors
   - Check if METRICS_ENABLED is set to true
   - Verify mana is actually running and processing requests

**Solutions**:
- Ensure MANA is exposing `/metrics` endpoint (METRICS_ENABLED=true)
- Fix ServiceMonitor or PodMonitor configuration
- Adjust network policies to allow Prometheus access
- Fix connectivity issues between Prometheus and mana service
- Fix Prometheus scraping configuration
- Ensure metrics aren't being filtered or transformed incorrectly
- Fix any issues preventing MANA from generating metrics
- Restart mana if needed to pick up configuration changes

### Issue: Logs Not Appearing in Log Collection System

**Symptoms**:
- MANA logs not appearing in Elasticsearch, Splunk, Loki, etc.
- Missing audit log entries
- Incomplete log data
- Gaps in log timeline

**Possible Causes**:
- Log collection agents not running or healthy
- Logs not being formatted correctly for collection
- Resource limits on log collection agents causing data loss
- Network issues between log sources and collection agents
- Log rotation or deletion causing premature loss
- Format mismatches (expecting JSON but getting plain text or vice versa)
- Timestamp parsing issues
- Severity or facility mismatches
- Agent configuration issues

**Diagnosis Steps**:
1. Check log collection agent status:
   ```bash
   kubectl get pods -l app=<log-agent>
   kubectl describe pod <log-agent-pod>
   kubectl -n <namespace> logs <log-agent-pod>
   ```
2. Test log generation from mana:
   ```bash
   kubectl -n mana logs <mana-pod>
   # Should see log output
   ```
3. Check network connectivity between mana and log collectors:
   ```bash
   # From a mana pod, test connectivity to log collector
   kubectl -n mana run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never \
     -- curl -v http://<log-collector-host>:<log-collector-port>/health
   ```
4. Verify log format matches what collector expects:
   - Is mana outputting JSON logs?
   - Does the collector expect JSON?
   - Are timestamps in expected format?
   - Are severity levels mapped correctly?
5. Check resource limits on log collectors:
   ```bash
   kubectl -n <namespace> top pods
   # Look for OOMKilled or throttling
   ```
6. Check log rotation or retention policies
7. Look for errors in log collector logs

**Solutions**:
- Fix log collection agent health and status
- Ensure mana is generating logs in expected format
- Fix resource limits on log collectors
- Resolve network connectivity issues
- Adjust log rotation or retention policies
- Fix format mismatches (ensure both ends agree on format)
- Fix timestamp parsing issues
- Correct severity or facility mappings
- Fix agent configuration issues
- Consider using a different log collection approach if current one problematic

### Issue: Health Check Endpoint Not Working

**Symptoms**:
- `/health` endpoint returns errors or unexpected responses
- `/health-check` endpoint not working as expected
- Unable to verify orchestrator or deployment health
- Monitoring systems showing false negatives or positives

**Possible Causes**:
- Routing issues (incorrect service or endpoint configuration)
- Controller or handler errors
- Dependency check failures (Redis, Kubernetes API)
- External service timeouts or failures
- Exception handling issues
- Misconfiguration of health check logic
- Resource exhaustion preventing execution
- Security or authentication issues blocking access

**Diagnosis Steps**:
1. Test the endpoint directly:
   ```bash
   # From within cluster
   curl -v http://mana-orchestrator.<namespace>.svc.cluster.local:8000/health

   # With more detail
   curl -v http://mana-orchestrator.<namespace>.svc.cluster.local:8000/health
   ```
2. Check mana logs for errors during health check execution
3. Verify dependency health checks:
   - Is Redis actually accessible?
   - Is Kubernetes API actually accessible?
   - Is GitHub API actually accessible (if configured)?
4. Check for exceptions or errors in the health check logic
5. Verify routing and service configuration:
   - Is the service pointing to the correct pods?
   - Are the pods actually running and ready?
6. Check for resource exhaustion during health check
7. Look for security or authentication issues blocking access
8. Check if any middleware or interceptors are interfering

**Solutions**:
- Fix routing or service configuration issues
- Fix controller or handler errors
- Fix dependency connection issues (Redis, Kubernetes API, GitHub)
- Fix external service timeouts or failures (address root causes)
- Fix exception handling issues
- Correct health check logic misconfiguration
- Address resource exhaustion (increase limits, fix leaks)
- Fix security or authentication issues blocking access
- Fix any middleware or interceptors interfering incorrectly

## Advanced Troubleshooting Techniques

### Using Ephemeral Debug Pods
For debugging connectivity or environmental issues:

```bash
# Create a debug pod with common tools
kubectl run -i --tty --rm debug --image=appropriate/curl:latest --restart=Never -- sh

# Inside the pod, you can:
# Test DNS resolution
nslookup <service>.<namespace>.svc.cluster.local

# Test HTTP connectivity
curl -v http://<service>.<namespace>.svc.cluster.local:<port>/<endpoint>

# Test Redis connectivity
redis-cli -h <redis-host> -p <redis-port> ping

# Test Kubernetes API connectivity
kubectl get ns

# Test network connectivity
ping <host>
traceroute <host>

# Check environment variables
env

# Check mounted volumes and files
ls -la /path/to/mount
cat /path/to/mount/file

# Exit when done
exit
```

### Using kubectl debug (Kubernetes 1.18+)
For debugging running pods:

```bash
# Debug a running pod
kubectl debug -it <pod-name> --image=appropriate/curl:latest --target=mana-orchestrator

# Or copy the pod's filesystem
kubectl debug <pod-name> --image=appropriate/curl:latest --share-processes --copy-to=debug-pod

# Then access the debug pod normally
kubectl attach debug-pod -c debug
```

### Using strace or similar for system calls
For debugging system-level issues:

```bash
# Run a pod with strace capability
kubectl run -i --tty --rm debug --image=appropriate/strace:latest --restart=Never -- strace -o /trace/log -f <command>

# Or add capabilities to existing pod (requires privileged)
kubectl patch deploy mana-orchestrator -p '{"spec":{"template":{"spec":{"containers":[{"name":"mana-orchestrator","securityContext":{"privileged":true}}]}}}}'
# Then exec in and run strace
kubectl -n mana exec <pod-name> -- strace -o /trace/log -f <command>
```

### Using perf or similar for performance profiling
For CPU or performance issues:

```bash
# Run a pod with perf capability
kubectl run -i --tty --rm debug --image=appropriate/perf:latest --restart=Never -- perf record -g -- <command>
# Then analyze with perf report

# Or use py-spy for Python applications
kubectl run -i --tty --rm debug --image=appropriate/py-spy:latest --restart=Never -- py-spy record -o /profile.svg -- <command>
```

### Using heap dumps for memory issues
For memory leak investigation:

```bash
# Generate heap dump (Java example)
kubectl -n mana exec <pod-name> -- jmap -dump:live,format=b,file=/heap.hprof <pid>
# Then analyze with MAT or similar

# For Python, use guppy or objgraph
kubectl -n mana exec <pod-name> -- python -c "import guppy; h=guppy.hpy(); h.heap()"
```

### Using network tracing
For network issues:

```bash
# Use tcpdump
kubectl run -i --tty --rm debug --image=appropriate/tcpdump:latest --restart=Never -- tcpdump -i any -w /capture.pcap

# Use wireshark or similar for analysis
# Or use tshark for command-line analysis
kubectl run -i --tty --rm debug --image=appropriate/tshark:latest --restart=Never -- tshark -i any -w /capture.pcap

# Then analyze the capture file
```

### Using strace for syscall tracing
For system call issues:

```bash
# Trace system calls
kubectl run -i --tty --rm debug --image=appropriate/strace:latest --restart=Never -- strace -o /syscall.log -f <command>

# Then analyze the syscall log
# Look for patterns, errors, delays, etc.
```

## Post-Incident Process

After resolving an issue, follow this process to prevent recurrence:

1. **Document the Incident**:
   - What happened?
   - When did it happen?
   - How was it detected?
   - What was the impact?
   - What was the root cause?
   - How was it resolved?
   - What was done to prevent recurrence?

2. **Update Documentation**:
   - Add troubleshooting steps to this guide
   - Update configuration documentation if needed
   - Update best practices if appropriate
   - Add known issues or workarounds if applicable
   - Update runbooks or playbooks

3. **Implement Preventive Measures**:
   - Add monitoring or alerting for similar issues
   - Improve documentation or training
   - Automate checks or validations
   - Improve testing or validation procedures
   - Fix underlying code issues if applicable
   - Improve deployment or configuration processes
   - Add safeguards or constraints

4. **Communicate Findings**:
   - Share with relevant teams
   - Update internal knowledge bases
   - Present in retrospectives or post-mortems
   - Consider blog posts or internal tech talks

5. **Review and Improve Process**:
   - How was the incident detected?
   - How long did it take to resolve?
   - How effective was the communication?
   - What could be improved in the response process?
   - What gaps were identified in monitoring or observability?
   - How can the incident response process be improved?

## Getting Help

If you're unable to resolve an issue using this guide:

1. **Check the FAQ**: Look for common questions and answers
2. **Search Existing Issues**: Look for similar problems in the GitHub issue tracker
3. **Ask in Community Forums**: Ask questions in appropriate community channels
4. **Open a GitHub Issue**: If you believe it's a bug or need feature suggestions
5. **Contact Support**: If you have a support agreement or contract
6. **Consult Experts**: Reach out to knowledgeable individuals or consulting firms

When asking for help, be sure to include:
- Clear description of the problem
- Steps to reproduce the issue
- Relevant logs and metrics
- Configuration details (sanitized)
- Environment details (Kubernetes version, cloud provider, etc.)
- What you've already tried to fix the problem
- Expected vs actual behavior
- Any error messages or codes
- Timestamps and frequency of the issue
- Impact and urgency

## Appendix: Common Error Messages and Their Meanings

### Redis Connection Errors
- `"Redis connection failed"`: Cannot establish connection to Redis
- `"Redis authentication failed"`: Invalid password or auth method
- `"Redis timeout"`: Connection or operation timed out
- `"Redis protocol error"`: Unexpected response from Redis
- `"Redis busy"`: Redis is currently busy (often during BGSAVE)

### Kubernetes API Errors
- `"Kubernetes config failed"`: Unable to load Kubernetes configuration
- `"Unauthorized"`: Missing or invalid authentication
- `"Forbidden"`: Insufficient permissions for the requested operation
- `"NotFound"`: Requested resource doesn't exist
- `"Conflict"`: Resource conflict (often during updates)
- `"ServiceUnavailable"`: Kubernetes API temporarily unavailable
- `"ServerTimeout"`: Request to Kubernetes API timed out

### GitHub API Errors
- `"Bad credentials"`: Invalid or missing GitHub token
- `"Validation Failed"`: Request failed validation (often missing required fields)
- `"Unauthorized"`: Missing or invalid authentication (401)
- `"Forbidden"`: Insufficient permissions (403) - often missing required scopes
- `"Resource Not Accessible By Integration"`: GitHub App doesn't have access to resource
- `"Service Unavailable"`: GitHub API temporarily unavailable
- `"Rate Limit Exceeded"`: Too many requests in too short time

### HTTP/Webhook Errors
- `"Bad Request"`: Invalid or malformed request (400)
- `"Unauthorized"`: Missing or invalid authentication (401)
- `"Forbidden"`: Insufficient permissions (403)
- `"Not Found"`: Resource not found (404)
- `"Method Not Allowed"`: HTTP method not allowed for endpoint (405)
- `"Not Acceptable"`: Cannot produce requested format (406)
- `"Conflict"`: Request conflicts with current state (409)
- `"Length Required"`: Missing Content-Length header (411)
- `"Precondition Failed"`: Precondition given in header failed (412)
- `"Payload Too Large"`: Request body too large (413)
- `"URI Too Long"`: URI too long (414)
- `"Unsupported Media Type"`: Invalid Content-Type (415)
- `"Range Not Satisfiable"`: Invalid range request (416)
- `"Expectation Failed"`: Expect header not met (417)
- `"Misdirected Request"`: Request sent to wrong server (421)
- `"Unprocessable Entity"`: Semantic errors in request (422)
- `"Locked"`: Resource is locked (423)
- `"Failed Dependency"`: Request failed due to dependency failure (424)
- `"Too Early"`: Indicates willingness to replay (425)
- `"Upgrade Required"`: Server requires protocol upgrade (426)
- `"Precondition Required"`: Required precondition missing (428)
- `"Too Many Requests"`: Rate limiting (429)
- `"Request Header Fields Too Large"`: Header fields too large (431)
- `"Unavailable For Legal Reasons"`: Unavailable for legal reasons (451)
- `"Internal Server Error"`: Unexpected condition prevented request fulfillment (500)
- `"Not Implemented"`: Server does not support required functionality (501)
- `"Bad Gateway"`: Invalid response from upstream server (502)
- `"Service Unavailable"`: Server unable to handle request (503)
- `"Gateway Timeout"`: Upstream server did not respond in time (504)
- `"HTTP Version Not Supported"`: HTTP version not supported (505)
- `"Variant Also Negotiates"`: Transparent content negotiation error (506)
- `"Insufficient Storage"`: Insufficient storage to complete request (507)
- `"Loop Detected"`: Loop detected while processing request (508)
- `"Not Extended"`: Further extensions required for request (510)
- `"Network Authentication Required"`: Network access requires authentication (511)

### Validation Errors
- `"must be >= 1"`: Value must be greater than or equal to 1
- `"must be > 0"`: Value must be greater than 0
- `"must be > X"`: Value must be greater than X
- `"must be between X and Y"`: Value must be between X and Y inclusive
- `"must be one of [X, Y, Z]"`: Value must be one of the specified values
- `"must match regex /pattern/"`: Value must match the given regular expression
- `"must be valid JSON"`: Value must be valid JSON
- `"must be a valid URL"`: Value must be a valid URL
- `"must be a valid email"`: Value must be a valid email address
- `"must be a valid Kubernetes quantity"`: Value must be a valid Kubernetes quantity string (e.g., 100m, 1Gi)
- `"must be a valid port number"`: Value must be a valid port number (1-65535)
- `"must be a valid ISO 8601 timestamp"`: Value must be a valid ISO 8601 timestamp
- `"must be a valid UUID"`: Value must be a valid UUID
- `"must be a valid semver"`: Value must be a valid semantic version string

## Conclusion

Troubleshooting MANA effectively requires a systematic approach, good observability, and understanding of the system's components and their interactions. By following the guidelines in this document, you should be able to diagnose and resolve most issues you encounter.

Remember that effective troubleshooting is as much about prevention as it is about reaction. Invest in good monitoring, clear documentation, regular testing, and continuous improvement to reduce the frequency and impact of issues.

If you encounter an issue not covered in this guide, please consider contributing your findings back to the community by updating this documentation or sharing your experience in the appropriate forums.