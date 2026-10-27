# Troubleshooting Guide

This guide helps you diagnose problems with MANA's installation, configuration, connectivity and recovery behaviour. Messages quoted here are the ones the code actually logs or returns, so you can search for them.

## Start Here

Most problems can be narrowed down with four commands:

```bash
# 1. Is the orchestrator up, and are its dependencies reachable?
curl -s $MANA/health | jq

# 2. Which settings is it really using?
curl -s $MANA/config | jq

# 3. What does it say?
kubectl -n orchestrator logs -l app=orchestrator --tail=200

# 4. What happened to the deployment in question?
curl -s $MANA/deployment/<deployment-id> | jq
```

(`$MANA` is your orchestrator URL; for example `kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000 &` then `export MANA=http://localhost:8000`.)

Remember the model: MANA reacts to **webhooks**. If a deployment seems ignored, first confirm the pipeline sent one.

### Triage by symptom

| Symptom                               | Go to                                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------ |
| Orchestrator pod will not start       | [Startup problems](#orchestrator-pod-crashloopbackoff-or-error)                            |
| `/health` says `degraded`             | [Redis](#cannot-connect-to-redis), [Kubernetes API](#cannot-connect-to-the-kubernetes-api) |
| Webhook returns 500 or 422            | [Webhook errors](#webhook-errors)                                                          |
| Response says `alert`                 | [Alert responses](#response-action-is-alert)                                               |
| Retries scheduled but nothing re-runs | [Retries do not re-run the pipeline](#retries-do-not-re-run-the-pipeline)                  |
| No rollback happens                   | [Rollbacks not happening](#rollbacks-not-happening)                                        |
| Rollback happens but fails            | [Rollbacks not succeeding](#rollbacks-not-succeeding)                                      |
| Health check always fails             | [Health checks always failing](#health-checks-always-failing)                              |
| No metrics, logs or dashboards        | [Monitoring](monitoring.md#troubleshooting-monitoring)                                     |

## Installation and Startup

### Orchestrator pod `CrashLoopBackOff` or `Error`

```bash
kubectl -n orchestrator get pods
kubectl -n orchestrator logs <pod> --previous
kubectl -n orchestrator describe pod <pod>
```

| Log line or event                                                                  | Cause                                   | Fix                                                                                                            |
| ---------------------------------------------------------------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `Cannot connect to Redis: ...`                                                     | Redis down or wrong `REDIS_HOST`        | See [Redis](#cannot-connect-to-redis)                                                                          |
| `Cannot load Kubernetes config: ...`                                               | No in-cluster config or kubeconfig      | Run in the cluster with a ServiceAccount, or provide a kubeconfig locally                                      |
| `ValueError: max_backoff must be > initial_backoff` (or another `... must be ...`) | Invalid configuration                   | Correct the variable; see [validation errors](../configuration/orchestrator.md#validation-errors)              |
| `ValueError: invalid literal for int()`                                            | Non-numeric value in a numeric variable | Fix the variable                                                                                               |
| Event `CreateContainerConfigError`, `secret "github-token" not found`              | Secret missing                          | `kubectl create secret generic github-token --from-literal=token=... -n orchestrator`                          |
| `ImagePullBackOff` / `ErrImagePull`                                                | `orchestrator:latest` not on the nodes  | `kind load docker-image orchestrator:latest` / `minikube image load`, or push to a registry and edit the image |
| `OOMKilled`                                                                        | Memory limit (512Mi) too low            | Raise `resources.limits.memory`                                                                                |

The orchestrator exits at startup if Redis or the Kubernetes config cannot be loaded, so it will restart until both work. The readiness and liveness probes wait 60 s before the first check, so a healthy start can take a minute.

### Pods `Pending` or `ContainerCreating`

```bash
kubectl -n orchestrator describe pod <pod> | sed -n '/Events/,$p'
kubectl describe nodes | grep -A6 "Allocated resources"
kubectl get resourcequota -A
```

Typical causes: insufficient CPU or memory on the nodes, a ResourceQuota (the Terraform `security` module sets quotas on `production`), unbound PersistentVolumeClaims (if you added one for Redis), or missing image pull credentials.

### Service not reachable

```bash
kubectl -n orchestrator get svc,endpoints orchestrator-service
kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000
curl -v localhost:8000/health
```

Empty endpoints mean no Ready pod matches the selector `app=orchestrator`. From inside the cluster use `http://orchestrator-service.orchestrator.svc:8000`. A `ClusterIP` service is not reachable from GitHub-hosted runners; see [CI/CD Integration](cicd-integration.md#1-run-the-orchestrator-and-make-it-reachable).

## Configuration

### Invalid values or no effect

- Validation errors crash the pod at startup (table above). The accepted ranges are in [Configuration Guide](../configuration/overview.md#validation-rules).
- Settings are read only at startup. After a change, `kubectl rollout restart deployment/orchestrator -n orchestrator` and confirm with `curl $MANA/config`.
- `kubectl set env` changes are overwritten by the next `kubectl apply` or `terraform apply`. Update the manifest or Terraform too.
- Only variables listed in [Environment Variables](../configuration/environment-variables.md) exist. `LOG_LEVEL` and `ENABLE_AUDIT_LOGGING` are applied at startup.
- With Terraform, `GITHUB_TOKEN` and `GITHUB_REPO` are not set on the orchestrator; `github_integration` in `/health` will be `false`.

## Connectivity

### Cannot connect to Redis

`/health` shows `"redis": "unhealthy"` and logs show `Cannot connect to Redis`.

```bash
kubectl -n orchestrator get pods,svc -l app=redis
kubectl -n orchestrator exec deploy/redis -- redis-cli ping           # PONG
kubectl -n orchestrator exec deploy/orchestrator -- env | grep REDIS
kubectl -n orchestrator exec deploy/orchestrator -- python -c \
  "import redis,os;print(redis.Redis(host=os.getenv('REDIS_HOST'),port=6379,socket_timeout=3).ping())"
```

Checks: the host must be `redis-service` in-cluster (`redis` in Compose, `localhost` locally); the Redis pod must be Ready; a NetworkPolicy must allow the orchestrator to reach port 6379; the client has no password support, so a Redis started with `--requirepass` (Terraform `redis_password`) will refuse it.

Data safety: the bundled Redis uses an `emptyDir`, so rescheduling it loses all state, including `last_successful:*` rollback targets. Send a fresh `success` webhook (or deploy once) to repopulate.

### Cannot connect to the Kubernetes API

`/health` shows `"kubernetes": "unhealthy"`, or logs show `Cannot load Kubernetes config`.

```bash
kubectl -n orchestrator get sa orchestrator-sa
kubectl -n orchestrator get deploy orchestrator -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl -n orchestrator exec deploy/orchestrator -- ls /var/run/secrets/kubernetes.io/serviceaccount
```

In-cluster the orchestrator uses the pod's ServiceAccount token. Locally it falls back to your kubeconfig, which must be readable by the process (in Compose, by the non-root container user) and must point to an address reachable from where the orchestrator runs (Kind kubeconfigs use `127.0.0.1`, which is wrong inside a container). If a NetworkPolicy restricts egress, it must allow the API server.

### Cannot reach application health endpoints

Symptoms: rollbacks never verify, `health_check_failed` audit events, `No pods found for deployment ...`.

```bash
kubectl -n production get pods -l app=sample-app -o wide          # labels and IPs
POD_IP=$(kubectl -n production get pod -l app=sample-app -o jsonpath='{.items[0].status.podIP}')
kubectl -n orchestrator exec deploy/orchestrator -- \
  python -c "import requests;r=requests.get('http://$POD_IP:5000/health',timeout=5);print(r.status_code,r.text)"
```

The orchestrator calls pod IPs directly. Check:

1. Pods carry the label `app=<deployment name>` (otherwise: `No pods found`).
2. `HEALTH_CHECK_PORT` and `HEALTH_CHECK_PATH` match the application.
3. The response is HTTP 200 with JSON containing `"status": "healthy"`.
4. **NetworkPolicies** allow the orchestrator namespace to reach the application pods. The shipped sample-app policy admits only `ingress-nginx` and `monitoring`, so you must add `orchestrator`.

### GitHub API problems

| Log message                                                                         | Meaning                                                     | Fix                                                                             |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `Cannot trigger GitHub workflow re-run: GITHUB_TOKEN or GITHUB_REPO not configured` | Missing variables                                           | Set both; restart                                                               |
| `GitHub API permission denied. Ensure GITHUB_TOKEN has 'actions:write' permission.` | HTTP 403                                                    | Grant _Actions: Read and write_ (fine-grained) or `repo` + `workflow` (classic) |
| `Failed to trigger re-run: 401 - ...`                                               | Invalid or expired token                                    | Replace the token                                                               |
| `Failed to trigger re-run: 404 - ...`                                               | Wrong `GITHUB_REPO` or run ID, or token cannot see the repo | Check `owner/repo` and the `workflow_run_id`                                    |
| `Failed to trigger re-run: 403/409/422 - ...`                                       | The run is still in progress or not re-runnable             | Let the run finish; lengthen the backoff                                        |
| `GitHub API request timeout`                                                        | Egress to `api.github.com` blocked or slow                  | Check proxies and NetworkPolicies                                               |

Test the token directly:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo>/actions/runs?per_page=1 | jq '.workflow_runs[0].id'
```

## Permissions

### Insufficient Kubernetes permissions

Symptoms: rollback fails with an HTTP 403 `ApiException`, or `forbidden` in logs.

```bash
kubectl auth can-i patch deployments -n production \
  --as=system:serviceaccount:orchestrator:orchestrator-sa
kubectl auth can-i list pods -n production \
  --as=system:serviceaccount:orchestrator:orchestrator-sa
kubectl get clusterrole orchestrator-role -o yaml
kubectl get clusterrolebinding orchestrator-binding -o yaml
```

The role needs `get/list/watch/update/patch` on Deployments and ReplicaSets and `get/list/watch` on Pods. If `can-i` says `no`, re-apply `orchestrator/k8s/deployment.yaml` and make sure the binding's subject namespace is `orchestrator`.

## Webhook Errors

| Response                                      | Cause                                                         | Fix                                                                                            |
| --------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `422`                                         | A required field is missing or has the wrong type             | Include `deployment_id`, `namespace`, `app_name`, `version`, `status`; build JSON with `jq`    |
| `422` mentioning `failure_type`               | Unknown `failure_type`, or `status: failed` without one      | Use `build_failure`, `test_failure`, `deployment_failure`, `health_check_failure`, `smoke_test_failure` or `timeout` |
| `500` with a Redis error                      | State could not be saved                                      | [Redis](#cannot-connect-to-redis)                                                              |
| Timeout on the client                         | A rollback is synchronous and can take up to 5 minutes        | Raise the client timeout                                                                       |
| Connection refused / no route                 | Orchestrator unreachable from the runner                      | Self-hosted runner, tunnel, or authenticated ingress                                           |

## Recovery Behaviour

### Response `action` is `alert`

| `message`                                                               | Meaning                                                                                                                                                          | Fix                                                                                                                                                   |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Cannot retry - missing workflow_run_id. Manual intervention required.` | The failure webhook carried no `metadata.workflow_run_id` and no earlier webhook for this deployment ID stored one | Include `metadata.workflow_run_id` on the failure webhook (the shipped deploy workflow does; the build-stage webhook does not) |
| `No previous version available - manual intervention required`          | No `last_successful:<ns>:<app>` key and no versioned ReplicaSet                                                                                                  | Report a `success` webhook for a good deployment; label pods with `version`                                                                           |
| `Rollback failed - manual intervention required`                        | The rolled-back Deployment did not become healthy in time                                                                                                        | [Rollbacks not succeeding](#rollbacks-not-succeeding)                                                                                                 |

Each of these also writes a `manual_intervention_required` audit event.

### Retries do not re-run the pipeline

1. Did the response say `action: retry`? If it said `alert`, see above.
2. Is `github_integration` `true` in `/health`?
3. After the backoff, look for the GitHub log messages in the table above.
4. GitHub re-runs only completed runs; the webhook is sent while the run is still finishing, so a short backoff can be rejected. Raise `INITIAL_BACKOFF`.
5. If the orchestrator pod restarted during the backoff, the pending retry is lost (it lives in memory). Re-run the workflow manually.
6. Each re-run must reuse the same `deployment_id` or the retry counter restarts ([CI/CD Integration](cicd-integration.md#recommended-workflow-changes)).

### Rollbacks not happening

A rollback needs: the failure count to exceed `MAX_RETRIES`, a known previous version, and the failed version to be the one running.

```bash
curl -s $MANA/config | jq '{max_retries}'
curl -s $MANA/deployment/<id> | jq '{status, retry_count, previous_version}'
kubectl -n orchestrator exec deploy/redis -- redis-cli get last_successful:<ns>:<app>
```

- With the defaults, rollback starts on the **fourth** failure for the same deployment ID.
- A `skip` answer means another version is live, so there was nothing to roll back; `alert` for a build or test failure means nothing was deployed.
- If every attempt uses a new deployment ID the count never grows.
- To roll back immediately, use `POST /rollback`.

### Rollbacks not succeeding

```bash
kubectl -n production get pods -l app=sample-app
kubectl -n production describe pod <pod> | sed -n '/Events/,$p'
kubectl -n production rollout status deployment/sample-app
kubectl -n production get deploy sample-app -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
```

- The **container name must equal the Deployment name** unless the pod has a single container; with several containers and no match the rollback fails and says so in `rollback_failed`.
- The target image tag must exist and be pullable (`ErrImagePull` otherwise).
- Resource quotas or scheduling problems can stop the old version from starting.
- Verification also needs the health endpoint to answer; see below.
- The decision after a failed rollback is `alert` and the state becomes `failed`.

### Health checks always failing

1. Run the manual check: `curl -s -X POST $MANA/health-check -H 'Content-Type: application/json' -d '{"namespace":"production","deployment_name":"sample-app","timeout":30}'`.
2. If `ready_replicas` never equals `replicas`, Kubernetes itself is not Ready; check probes and events.
3. If replicas are Ready but the check fails, test the endpoint from the orchestrator pod ([steps above](#cannot-reach-application-health-endpoints)). After three consecutive application failures the check gives up.
4. Mind the payload: the endpoint must return `{"status": "healthy"}`, not just 200.

## Performance and Resources

| Symptom                    | Checks                                                                                                                      |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| High memory or `OOMKilled` | `kubectl top pod -n orchestrator`; large Redis keyspaces affect `GET /deployments/recent` (it scans all keys); raise limits |
| High CPU                   | Many concurrent rollbacks or health checks; check request rate in `rate(deployments_total[1m])`                             |
| Slow webhook responses     | Rollbacks are synchronous and wait for health; that is expected. Failures with long timeouts (300 s) keep requests open     |
| Slow `/deployments/recent` | Reduce `limit`, or keep fewer keys (state expires after 24 h)                                                               |

Metrics and dashboards: [Monitoring](monitoring.md).

## Advanced Techniques

### Debug pod with network tools

```bash
kubectl -n orchestrator run debug --rm -it --image=nicolaka/netshoot --restart=Never -- bash
# inside:
nslookup redis-service
curl -s http://orchestrator-service:8000/health
curl -s http://<pod-ip>:5000/health
redis-cli -h redis-service ping
curl -sk https://kubernetes.default.svc/healthz
```

### `kubectl debug`

```bash
kubectl -n orchestrator debug -it deploy/orchestrator --image=busybox --target=orchestrator
kubectl -n orchestrator debug pod/<pod> -it --copy-to=orch-debug --container=orchestrator -- sh
```

### Reproduce locally with mocks

The unit tests mock Redis, Kubernetes and GitHub, so you can reproduce decision logic without a cluster:

```bash
cd orchestrator && pytest tests/ -v -k failure
```

### Follow one deployment through the logs

```bash
kubectl -n orchestrator logs -l app=orchestrator --since=30m | grep "<deployment-id>"
```

## After an Incident

1. Confirm service is restored (health check, pods Ready, traffic).
2. Collect: `GET /deployment/<id>`, the audit lines, `kubectl rollout history`, and the pipeline run URL.
3. Identify why the release failed and why recovery did or did not work (missing `workflow_run_id`, no rollback target, health path mismatch, a blocked network path).
4. Fix the root cause and, if MANA misbehaved, open an issue with the evidence (secrets removed).
5. Adjust thresholds or add a test so it is caught next time.

## Getting Help

- Search [GitHub Issues](https://github.com/othaime-en/mana/issues).
- Open a new issue with: what you expected, what happened, MANA version or commit, how it is deployed, `curl $MANA/config` and `curl $MANA/health` output, and relevant logs with secrets removed.
- Security problems: follow the [Security policy](../about/security.md#reporting-a-vulnerability), not a public issue.

## Frequently Asked Questions

**Does MANA watch my cluster for failing Deployments?**
No. It reacts to webhooks from your pipeline. A Deployment that breaks on its own (for example a crash after a successful rollout) is not detected unless something sends a failure event.

**What does MANA roll back to?**
The version in `last_successful:<namespace>:<app>` (the last `success` webhook). If that is missing, it uses the second-newest ReplicaSet that has a `version` label.

**Do I need GitHub for rollbacks?**
No. GitHub (`GITHUB_TOKEN`, `GITHUB_REPO`) is only used to re-run workflows for retries.

**How many failures before a rollback?**
By default four failures for the same deployment ID: three retries, then rollback.

**Why did my first failure return `alert`?**
The failure webhook had no `metadata.workflow_run_id`, so a retry cannot be scheduled. The ID is read from the webhook that reports the failure (or from earlier state for the same deployment ID).

**Can I run MANA without Redis?**
No. Startup fails without it.

**Is it safe to run two replicas?**
They share state through Redis, but there is no leader election; a duplicated webhook could be processed by both. Use one replica for strict single-actor behaviour.

**How long is state kept?**
Deployment records for 24 hours. Rollback targets (`last_successful:*`) do not expire.

**Can I use GitLab, Jenkins or another CI?**
You can send the webhook from any system. Automatic retries only work with GitHub Actions today.

**How do I secure the API?**
There is no built-in authentication. Restrict network access and see [Security](../about/security.md).

**Why are some dashboard panels or alerts empty?**
They depend on metrics the orchestrator does not emit yet; see [Monitoring](monitoring.md#prometheus-metrics).

## Appendix: Common Messages

### Startup

| Message                                                                                                                                                                                                                                                    | Meaning                                              |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `Cannot connect to Redis: <error>`                                                                                                                                                                                                                         | Redis unreachable; process exits                     |
| `Cannot load Kubernetes config: <error>`                                                                                                                                                                                                                   | No ServiceAccount token or kubeconfig; process exits |
| `max_retries must be >= 1`, `initial_backoff must be > 0`, `max_backoff must be > initial_backoff`, `backoff_multiplier must be > 1.0`, `health_check_timeout must be > 0`, `health_check_port must be between 1-65535` | Validation failures                                  |

### Runtime

| Message                                              | Meaning                                    |
| ---------------------------------------------------- | ------------------------------------------ |
| `No pods found for deployment <name>`                | No running pods with label `app=<name>`    |
| `Deployment <name> health check timeout`             | Verification exceeded its timeout          |
| `Rollback failed for <name>`                         | Rollback did not become healthy            |
| `Failed to save deployment state: <error>`           | Redis write failed                         |
| `Error processing webhook: <error>`                  | Unexpected exception; response is HTTP 500 |
| `Error during manual rollback: <error>`              | `POST /rollback` failed                    |
| `Error checking Redis for previous version: <error>` | Falls back to ReplicaSets                  |

### HTTP responses

| Code | Typical source                                                          |
| ---- | ----------------------------------------------------------------------- |
| 200  | Success (including `action: alert`; check the body)                     |
| 404  | `GET /deployment/<id>` for an unknown or expired ID                     |
| 422  | Invalid request body                                                    |
| 500  | Rollback failed, Redis or Kubernetes errors |
