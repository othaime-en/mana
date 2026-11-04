# Recovery Policy

The recovery policy decides what MANA does about each failure: retry it, roll back at once, or only alert a human. It replaces the single "retry N times, then roll back" counter. Without any configuration MANA uses the built-in policy below; `POLICY_FILE` points at a file to change it.

## The built-in policy

| Failure type           | Action                                       | Why                                                                          |
| ---------------------- | -------------------------------------------- | ---------------------------------------------------------------------------- |
| `build_failure`        | retry twice, then alert                      | CI infrastructure flakes; nothing was deployed, so there is nothing to roll back |
| `test_failure`         | alert                                        | A failing test fails again; retrying wastes a pipeline run                   |
| `deployment_failure`   | retry once, then roll back                   | The rollout failed; one retry covers a transient cause                       |
| `health_check_failure` | roll back at once                            | The new version runs and is unhealthy: restore the old one now               |
| `smoke_test_failure`   | roll back at once                            | Same                                                                         |
| `timeout`              | retry once, then roll back                   | Also what the [rollout watcher](#the-rollout-watcher) reports                 |

`MAX_RETRIES` stays a ceiling: no retry rule retries more often than that, whatever the file says.

## How a decision is made

For every failed webhook MANA looks at four things and passes them to a pure function, `decide()`:

1. The **rule** for the failure type, from the first override that matches the namespace and application, otherwise the default.
2. The **attempt**: how many failures this deployment ID has had, including this one.
3. Whether a **retry is possible**: it needs a `workflow_run_id` to re-run.
4. The **diagnosis** of the cluster (below) and whether a rollback **target** exists (a last successful version different from the failed one).

A `retry` rule retries while the attempts last. It continues with its `then` action (`rollback` or `alert`, default `alert`) when the attempts are used up, when no retry is possible, or when the diagnosis says the cause is deterministic. A rollback without a target becomes an alert. Every decision is written to the audit log as `policy_decision` and counted in `mana_policy_decisions_total{failure_type,action,reason}`; `reason` is one of `policy`, `retries_exhausted`, `retry_unavailable`, `deterministic_failure`, `no_rollback_target`.

A rollback is still guarded as before: only when the live image tag is the failed version (otherwise `skip`), never for build or test failures, and not while the [circuit breaker](#circuit-breaker-and-deny-list) is open.

## Diagnosis

For failures after something was deployed MANA reads the pods running the failed version (matched by image tag) and the Deployment, and names the cause:

| Cause                                                                                              | Kind          | Effect on a `retry` rule              |
| -------------------------------------------------------------------------------------------------- | ------------- | ------------------------------------- |
| `ImagePullBackOff`, `ErrImagePull`, `Unschedulable`                                                | transient     | retries as the rule says              |
| `CrashLoopBackOff`, `OOMKilled`, `CreateContainerConfigError`, `CreateContainerError`, `InvalidImageName`, `RunContainerError`, `ProgressDeadlineExceeded` | deterministic | skips the retries, uses `then` at once |

Normal start-up states such as `ContainerCreating` are not failures. If the cluster cannot be read, there is simply no diagnosis and the rule applies as written. The diagnosis is stored in the deployment state (`diagnosis`), appears in alerts and in `mana_failure_diagnoses_total{namespace,reason}`.

## The policy file

Copy [`orchestrator/policy.example.yaml`](https://github.com/othaime-en/mana/blob/main/orchestrator/policy.example.yaml) and set `POLICY_FILE`:

```yaml
version: 1
default:
  build_failure:        {action: retry, max_attempts: 2}
  test_failure:         {action: alert}
  deployment_failure:   {action: retry, max_attempts: 1, then: rollback}
  health_check_failure: {action: rollback}
  smoke_test_failure:   {action: rollback}
  timeout:              {action: retry, max_attempts: 1, then: rollback}
overrides:
  - match: {namespace: staging}
    rules:
      health_check_failure: {action: retry, max_attempts: 2, then: rollback}
rollback:
  verify_timeout_seconds: 120
  freeze_failed_version: true
  deny_ttl_seconds: 604800
circuit_breaker:
  max_rollbacks: 3
  window_seconds: 3600
```

- `default` must list every failure type. An override lists only the types it changes, under `rules` (not `on`: YAML 1.1 reads an unquoted `on` as the boolean `true`). The first override whose `match` fits (`namespace`, `app`, either or both) and that lists the type wins.
- `build_failure` and `test_failure` can never roll back, as a rule or as `then`: they happen before anything is deployed.
- Unknown keys, unknown failure types, a retry without `max_attempts` and `then: retry` are rejected, and the orchestrator refuses to start with a message naming the field.
- `rollback.verify_timeout_seconds` is how long a rolled-back version has to become healthy.

Mount the file from a ConfigMap:

```bash
kubectl -n orchestrator create configmap mana-policy --from-file=policy.yaml=orchestrator/policy.example.yaml
kubectl -n orchestrator patch deployment orchestrator --patch '
spec:
  template:
    spec:
      volumes: [{name: policy, configMap: {name: mana-policy}}]
      containers:
        - name: orchestrator
          volumeMounts: [{name: policy, mountPath: /etc/mana, readOnly: true}]
          env: [{name: POLICY_FILE, value: /etc/mana/policy.yaml}]'
```

The file is read at start-up; restart the orchestrator to apply a change.

## Circuit breaker and deny-list

After a verified rollback MANA does two things, both stored in Redis and shared by every replica:

- **Deny-list.** The failed version is denied for `deny_ttl_seconds` (unless `freeze_failed_version: false`). When a pipeline reports `in_progress` for it, the orchestrator answers `status: "denied"`, counts and alerts it, and persists nothing. `scripts/notify-orchestrator.sh` exits 3 on that answer and the deploy workflow stops before deploying and reports no failure, so a refused version does not start a retry cycle. Lift a denial with `DELETE /denied-versions/{namespace}/{app}/{version}`.
- **Circuit breaker.** Each finished rollback is counted per application. More than `max_rollbacks` inside `window_seconds` pauses automatic rollback for that application: the next failure alerts instead (`CircuitBreakerOpen`). Retries are not affected and the breaker closes by itself as rollbacks leave the window. `GET /deployments/{ns}/{app}/status` shows the breaker and the denied versions under `automation`.

If Redis cannot be read these guards fail open: nothing is denied and the breaker stays closed.

## The rollout watcher

A webhook can be lost, and deployments made with `kubectl`, Helm or Argo CD send none. With `WATCHER_ENABLED=true` the orchestrator lists the Deployments labelled `mana.io/managed=true` in `WATCHER_NAMESPACES` every `WATCHER_INTERVAL_SECONDS` and treats a stuck rollout as a `timeout` failure, handled by the same policy (so by default it rolls back).

A rollout is stuck when the controller gave up (`Progressing=False`, `ProgressDeadlineExceeded`), or when it is unfinished and `Progressing` has not moved for `WATCHER_STUCK_SECONDS` (default 120 s, before the 600 s progress deadline). A finished rollout that later lost a pod is degraded, not stuck. Images pinned by digest cannot be tracked and are skipped with a warning.

Every replica runs the watcher. A rollout is handled once: its deployment ID comes from the Deployment generation, a webhook that already dealt with the version is respected, and a 60 s Redis claim lets one replica act. It needs `list` on Deployments in each watched namespace (`orchestrator/k8s/rbac.yaml` grants it) and is off unless enabled. `mana_watcher_detections_total` counts detections and `WatcherScanFailing` fires when listing fails.

## From the old behaviour

With the built-in policy a failed health check rolls back on the first failure, a test failure alerts at once, `deployment_failure` retries once, and a failure without a `workflow_run_id` continues with the rule's follow-up (a rollback for deployment failures) instead of alerting. To keep the old "three retries, then roll back" for deployment failures set that rule to `{action: retry, max_attempts: 3, then: rollback}`.

## Measuring it

`make demo MODE=crash` (see `scripts/demo/simulate-failure.sh`) deploys a broken version using the sample app's [fault injection](sample-app.md#fault-injection), reports the failure and prints the recovery time as `mana_recovery_duration_seconds` defines it. The target is a p95 under 60 s for post-deploy failures; `SlowRollbackRecovery` fires above it. `HEALTH_POLL_SECONDS` (default 5) sets how often a rollout is checked while verifying.
