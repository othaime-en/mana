# Recovery Policy

The recovery policy decides what MANA does about each failure: retry it, roll back at once, or only alert a human. Without any configuration MANA uses the built-in policy below. The orchestrator manifests ship it as the `mana-policy` ConfigMap, mounted at `/etc/mana/policy.yaml`, so you can change it without rebuilding anything.

## The built-in policy

| Failure type           | Action                       | Why                                                                              |
| ---------------------- | ---------------------------- | -------------------------------------------------------------------------------- |
| `build_failure`        | retry twice, then alert      | CI infrastructure flakes; nothing was deployed, so there is nothing to roll back |
| `test_failure`         | alert                        | A failing test fails again; retrying wastes a pipeline run                       |
| `deployment_failure`   | retry once, then roll back   | The rollout failed; one retry covers a transient cause                           |
| `health_check_failure` | roll back at once            | The new version runs and is unhealthy: restore the old one now                   |
| `smoke_test_failure`   | roll back at once            | Same                                                                             |
| `timeout`              | retry once, then roll back   | A pipeline step timed out                                                        |
| `rollout_stuck`        | roll back at once            | What the [rollout watcher](#the-rollout-watcher) reports                         |
| `verification_failure` | roll back at once            | What [post-deploy verification](#post-deploy-verification) reports              |

`MAX_RETRIES` stays a ceiling: no retry rule retries more often than that, whatever the file says.

## How a decision is made

For every failure MANA looks at four things and passes them to a pure function, `decide()`:

1. The **rule** for the failure type, from the first override that matches the namespace and application, otherwise the default.
2. The **attempt**: how many failures *of this type* this deployment ID has had, including this one. A build failure does not use up the attempts of a deployment failure. (The state also keeps the total as `retry_count`, which job IDs need.)
3. Whether a **retry is possible**: it needs a `workflow_run_id` to re-run.
4. The **diagnosis** of the cluster (below) and whether a rollback **target** exists (a last successful version different from the failed one).

A `retry` rule retries while the attempts last. It continues with its `then` action (`rollback` or `alert`, default `alert`) when the attempts are used up, when no retry is possible, or when the diagnosis says the cause is deterministic. A rollback without a target becomes an alert. Every decision is written to the audit log as `policy_decision` and counted in `mana_policy_decisions_total{failure_type,action,reason}`; `reason` is one of `policy`, `retries_exhausted`, `retry_unavailable`, `deterministic_failure`, `no_rollback_target`.

A rollback is still guarded: only when the live image tag is the failed version (otherwise `skip`), never for build or test failures, and not while the [circuit breaker](#circuit-breaker-and-deny-list) is open.

States saved before attempts were counted per type are read correctly: their earlier failures are attributed to the failure type they recorded.

## Diagnosis

For failures after something was deployed MANA reads the pods running the failed version (matched by image tag) and the Deployment, and names the cause:

| Cause                                                                                                                                                       | Kind          | Effect on a `retry` rule               |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | -------------------------------------- |
| `ImagePullBackOff`, `ErrImagePull`, `Unschedulable`                                                                                                         | transient     | retries as the rule says               |
| `CrashLoopBackOff`, `OOMKilled`, `CreateContainerConfigError`, `CreateContainerError`, `InvalidImageName`, `RunContainerError`, `ProgressDeadlineExceeded` | deterministic | skips the retries, uses `then` at once |

Normal start-up states such as `ContainerCreating` are not failures. If the cluster cannot be read, there is simply no diagnosis and the rule applies as written. The diagnosis is stored in the deployment state (`diagnosis`), appears in alerts and in `mana_failure_diagnoses_total{namespace,reason}`.

## The policy file

[`orchestrator/policy.example.yaml`](https://github.com/othaime-en/mana/blob/main/orchestrator/policy.example.yaml) documents every setting. The shipped ConfigMap is `orchestrator/k8s/policy-configmap.yaml`:

```yaml
version: 1
default:
  build_failure:        {action: retry, max_attempts: 2}
  test_failure:         {action: alert}
  deployment_failure:   {action: retry, max_attempts: 1, then: rollback}
  health_check_failure: {action: rollback}
  smoke_test_failure:   {action: rollback}
  timeout:              {action: retry, max_attempts: 1, then: rollback}
  rollout_stuck:        {action: rollback}
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

- A failure type left out of `default` uses the built-in rule, so a file written before a type existed keeps working, and a file may contain only `overrides`. An override lists only the types it changes, under `rules` (not `on`: YAML 1.1 reads an unquoted `on` as the boolean `true`). The first override whose `match` fits (`namespace`, `app`, either or both) and that lists the type wins.
- `build_failure` and `test_failure` can never roll back, as a rule or as `then`: they happen before anything is deployed.
- Unknown keys, unknown failure types, a retry without `max_attempts` and `then: retry` are rejected, with a message naming the field.
- `rollback.verify_timeout_seconds` is how long a rolled-back version has to become healthy.

### Changing the policy while MANA runs

Edit the ConfigMap and apply it:

```bash
kubectl -n orchestrator edit configmap mana-policy
```

Kubernetes takes up to about a minute to update the mounted file; the orchestrator then reloads it within `POLICY_RELOAD_SECONDS` (default 5; `0` turns reloading off). Each replica reloads on its own. A change is recognised by the file's inode, size and modification time, which also catches the symlink swap a ConfigMap update uses.

A file that is not valid **never replaces the policy in force**: the orchestrator logs `Policy reload rejected, keeping the policy in force` with the reason once per edit, counts it in `mana_policy_reloads_total{outcome="rejected"}`, and the `PolicyReloadRejected` alert fires. A file that is missing for a moment is ignored. An invalid file at start-up still stops the service. `GET /config` shows the policy file, the interval and when it was last loaded.

Deployed with Terraform, the module creates the same ConfigMap from the same manifest file.

## Circuit breaker and deny-list

After a verified rollback MANA does two things, both stored in Redis and shared by every replica:

- **Deny-list.** The failed version is denied for `deny_ttl_seconds` (unless `freeze_failed_version: false`). When a pipeline reports `in_progress` for it, the orchestrator answers `status: "denied"`, counts and alerts it, and persists nothing. `scripts/notify-orchestrator.sh` exits 3 on that answer and the deploy workflow stops before deploying and reports no failure, so a refused version does not start a retry cycle. Lift a denial with `DELETE /denied-versions/{namespace}/{app}/{version}`.
- **Circuit breaker.** Each finished rollback is counted per application. More than `max_rollbacks` inside `window_seconds` pauses automatic rollback for that application: the next failure alerts instead (`CircuitBreakerOpen`). Retries are not affected and the breaker closes by itself as rollbacks leave the window. `GET /deployments/{ns}/{app}/status` shows the breaker and the denied versions under `automation`.

If Redis cannot be read these guards fail open: nothing is denied and the breaker stays closed.

## The rollout watcher

A webhook can be lost, and deployments made with `kubectl`, Helm or Argo CD send none. With `WATCHER_ENABLED=true` the orchestrator lists the Deployments labelled `mana.io/managed=true` in `WATCHER_NAMESPACES` every `WATCHER_INTERVAL_SECONDS` and reports a stuck rollout as a `rollout_stuck` failure, handled by the same policy (so by default it rolls back). `rollout_stuck` is separate from `timeout`, so you can give watcher-detected rollouts their own rule without changing how a pipeline timeout is handled.

A rollout is stuck when the controller gave up (`Progressing=False`, `ProgressDeadlineExceeded`), or when it is unfinished and `Progressing` has not moved for `WATCHER_STUCK_SECONDS` (default 120 s, before the 600 s progress deadline). A finished rollout that later lost a pod is degraded, not stuck.

An image pinned by digest, or without a tag, cannot be matched to a rollback target. The watcher still reports it, named by a short digest (or `latest`), and the rollback guard turns the decision into an alert: you are told instead of the rollout being ignored.

Every replica runs the watcher. A rollout is handled once: its deployment ID comes from the Deployment generation (a spec change starts a new rollout), a webhook that already dealt with the version is respected, and a 60 s Redis claim lets one replica act. It acts only on Deployments that opted in with the label. It needs `list` on Deployments in each watched namespace (`orchestrator/k8s/rbac.yaml` grants it). `mana_watcher_detections_total` counts detections and `WatcherScanFailing` fires when listing fails.

## Post-deploy verification

A deployment whose pods are Ready and whose `/health` answers can still be bad: more errors, slower responses, less traffic. Verification checks a deployment against Prometheus **before** its version counts as a rollback target.

It is off by default. To use it, set `PROMETHEUS_URL` (the shipped manifests point at the kube-prometheus-stack service) and enable the section in the policy:

```yaml
verification:
  enabled: true
  namespaces: [production]
  warmup_seconds: 60
  bake_seconds: 180
  interval_seconds: 15
  consecutive_breaches: 3
  on_missing: ignore
  checks:
    - name: error-rate
      query: >-
        sum(rate(flask_http_request_total{namespace="{{namespace}}",pod=~"{{app}}-.+",status=~"5.."}[1m]))
        / sum(rate(flask_http_request_total{namespace="{{namespace}}",pod=~"{{app}}-.+"}[1m]))
      max: 0.05
```

How it works:

1. The pipeline reports `success`. Instead of settling, the orchestrator answers `status: "verifying"` and holds the deployment in the `verifying` state. Its version is **not** recorded as the last successful version yet.
2. Nothing is evaluated for `warmup_seconds`, so the old pods' samples leave the rate windows. Then every `interval_seconds` each check runs its query and compares the value with its `max` or `min`.
3. A check that breaches `consecutive_breaches` times in a row fails the deployment as a `verification_failure`, which the policy handles like any failure: a rollback by default, through the same lock, guards, deny-list and circuit breaker. The version is never recorded as good, so a rollback never lands on an unverified version.
4. If `bake_seconds` pass without that, the deployment succeeds and the version becomes the rollback target.

Details that matter:

- **Queries** must return one series (aggregate them) and use `{{namespace}}` and/or `{{app}}`; a query with neither is rejected at load, because it would measure every application. A namespace or application name that could break out of a PromQL label matcher is refused at run time, not escaped. The sample app's pods are named `<app>-<hash>-<id>`; the `version` label on its metrics comes from a pod label that `kubectl set image` does not update, so do not select on it.
- **No data and errors.** An idle application returns no data for an error-rate query, and Prometheus can be unreachable. By default (`on_missing: ignore`) neither counts as a breach nor resets a streak; both are counted in `mana_verification_checks_total{check,result}`, and `VerificationQueryFailing` fires when queries keep failing, because a deployment verified on no evidence is a risk. `on_missing: breach` makes them count as breaches.
- **Superseded.** If a newer deployment replaces the one being verified, its verification ends without recording a target.
- **Turning it off mid-bake**, or losing Prometheus, settles running verifications as if verification were off. If the verify job cannot run at all, the version is recorded unverified and a warning alert says so.
- **Durable.** Progress is kept in the deployment state and each round is a job in the Redis queue, so any replica continues after a restart.
- **Cost.** A deployment takes `bake_seconds` longer to count as good, and that time is part of `deployment_duration_seconds`. Each end is audited as `verification_result` and counted in `mana_verifications_total{namespace,outcome}`.
- Thresholds are absolute. Comparing against the previous version is not built.

`make demo MODE=errors DEMO_ARGS="--load-url http://localhost:8080/api/data"` (or `MODE=slow`) deploys a version that passes the pipeline's checks and is only caught by verification.

## From the old behaviour

With the built-in policy a failed health check rolls back on the first failure, a test failure alerts at once, `deployment_failure` retries once, and a failure without a `workflow_run_id` continues with the rule's follow-up (a rollback for deployment failures) instead of alerting. To keep the old "three retries, then roll back" for deployment failures set that rule to `{action: retry, max_attempts: 3, then: rollback}`. The watcher now reports `rollout_stuck` instead of `timeout`.

## Measuring it

`make demo MODE=crash` (see `scripts/demo/simulate-failure.sh`) deploys a broken version using the sample app's [fault injection](sample-app.md#fault-injection), reports the failure and prints the recovery time as `mana_recovery_duration_seconds` defines it. The target is a p95 under 60 s for post-deploy failures; `SlowRollbackRecovery` fires above it. `HEALTH_POLL_SECONDS` (default 5) sets how often a rollout is checked while verifying a rollback.
