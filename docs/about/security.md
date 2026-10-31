# Security

MANA has the power to change live Kubernetes Deployments, so its security posture matters. This page describes the security model as implemented, the gaps you must close before using it with anything important, a hardening checklist, and how to report a vulnerability.

!!! warning "Project status"
MANA is a learning and portfolio project. It has not been security audited. Treat the defaults as **development defaults**, and work through the [hardening checklist](#hardening-checklist) before any shared or production use.

## Threat Model

| Asset                             | Threat                                                                                        | Impact                                                               |
| --------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Orchestrator API                  | Anyone who can reach it can submit forged webhooks or call `POST /rollback`                   | Unwanted rollbacks, forced alerts, denial of service for deployments |
| Orchestrator ServiceAccount       | Pod compromise gives cluster-wide ability to patch Deployments                                | Arbitrary image changes in any namespace                             |
| `GITHUB_TOKEN`                    | Leak allows re-running (and, depending on scope, modifying) workflows                         | CI abuse, possible repository access                                 |
| `KUBE_CONFIG` secret in GitHub    | Leak gives cluster access with that kubeconfig's rights                                       | Cluster compromise                                                   |
| Redis                             | Unauthenticated access allows rewriting state, including `last_successful:*` rollback targets | Rollbacks to attacker-chosen versions                                |
| Container images and dependencies | Vulnerable or tampered packages                                                               | Remote code execution in the orchestrator or app                     |
| Monitoring stack                  | Default Grafana credentials, open dashboards                                                  | Information disclosure, tampering with alerts                        |

## What Is Implemented

| Area | Implementation | Where |
| ---- | -------------- | ----- |
| Webhook authentication | HMAC-SHA256 over `<timestamp>.<body>`, 300 s window, constant-time comparison, several secrets accepted for rotation; unsigned requests get `401` and increment `webhook_errors_total{reason="unauthorized"}` | `orchestrator/src/auth.py`, `scripts/notify-orchestrator.sh` |
| Operator endpoints | `X-API-Key` on `/rollback`, `/health-check`, `/config` and the state endpoints; a test fails if a route is neither guarded nor on the public list | `orchestrator/src/api.py` |
| Fail-closed start | The orchestrator exits without `WEBHOOK_SECRET` and `API_KEY` (32 characters or more) unless `AUTH_DISABLED=true`, which logs at CRITICAL | `orchestrator/src/config.py` |
| Generated secrets | `scripts/create-secrets.sh` writes `orchestrator-secrets` with random values and never prints them; Terraform uses `random_password`; no credential is committed | `scripts/`, Terraform |
| Redis | `--requirepass` from the Secret, client password and optional TLS (`REDIS_TLS`), reachable only from orchestrator pods | `orchestrator/k8s/redis.yaml`, `config.py` |
| Network segmentation | Default deny in `production`, `staging` and `orchestrator`; explicit allow rules selected with `kubernetes.io/metadata.name`; orchestrator ingress only from `monitoring` and `ci-runners` | `kubernetes/security/network-policies.yaml`, Terraform `security` module |
| Namespaced RBAC | The orchestrator has `get`/`patch` on Deployments and `list` on ReplicaSets and Pods in `production`, `staging` and `canary` only; a test ties the Role to the Kubernetes calls in the code | `orchestrator/k8s/rbac.yaml`, Terraform `orchestrator` module |
| Pod hardening | Pod Security `restricted` enforced on `production`, `staging`, `canary`, `orchestrator`; `seccomp` `RuntimeDefault`, read-only root filesystem with an `emptyDir` at `/tmp`, no privilege escalation, all capabilities dropped; no service-account token for the sample app and Redis | manifests, Terraform |
| CI supply chain | Every action pinned to a commit SHA, Dependabot, least-privilege `permissions`, `GITHUB_TOKEN` for the registry, secrets passed through `env:`, blocking `pip-audit`, Trivy (fixable CRITICAL) and Bandit | `.github/` |
| CI deploy identity | A namespaced `ci-deployer` ServiceAccount and Roles for the deploy workflow | `kubernetes/security/ci-deployer-rbac.yaml` |
| Resource limits | Requests and limits on every workload; production quota and LimitRange | manifests, `resource-quotas.yaml` |
| Auditability | Structured audit events for every recovery action | `utils/audit.py` |

## Rotating secrets

```bash
scripts/create-secrets.sh --rotate                 # new redis-password, webhook-secret, api-key
kubectl -n orchestrator rollout restart deployment/redis deployment/orchestrator
kubectl -n orchestrator get secret orchestrator-secrets -o jsonpath='{.data.webhook-secret}' \
  | base64 -d | gh secret set MANA_WEBHOOK_SECRET
```

For a rotation without rejected webhooks, set `WEBHOOK_SECRET` to `<new>,<old>` first, update the GitHub secret, then drop the old value. Rotating the Redis password requires restarting Redis and the orchestrator together.

## Known Gaps

1. **Replay.** A valid webhook can be replayed for 300 seconds, and two replicas can both act on one event. Idempotency keys and locks are planned (CORE-06, FEAT-04).
2. **Open read endpoints.** `/`, `/health`, `/metrics` and the OpenAPI routes carry no credential, because probes and Prometheus use them. The NetworkPolicy is the control.
3. **NetworkPolicy enforcement is not proven on a cluster.** `scripts/test-network-policies.sh` and `network-policy-test.yml` check five allowed and six denied paths on Kind with Calico, but they have not been run. Egress to the API server and `api.github.com` is `0.0.0.0/0` on 443 and 6443; narrow it to the API server address.
4. **Redis is plain TCP in the cluster and not durable.** The password and the policy protect it; it still stores data on an `emptyDir` (INFRA-04). Use `REDIS_TLS` with a managed Redis.
5. **Terraform state holds the generated secrets.** Use an encrypted remote backend (INFRA-09) or an external secret store (Sealed Secrets, External Secrets Operator).
6. **CI credentials are long-lived.** `KUBE_CONFIG` can come from the `ci-deployer` ServiceAccount, but OIDC federation is not implemented, and the self-hosted runner namespace `ci-runners` is an assumption.
7. **Images are deployed by tag**, there is no SBOM or signature (FEAT-14), and several action majors are old (CI-04).
8. **`monitoring` has no Pod Security labels** because its charts need privileged pods, and Alertmanager is not deployed (OBS-06).
9. **Not verified outside the repository's tests:** the read-only root filesystem under gunicorn, Pod Security admission, the Trivy gate, and the first GitHub run of the workflows.

## Hardening Checklist

- [x] Webhook signature and operator API key (`WEBHOOK_SECRET`, `API_KEY`)
- [x] Orchestrator ingress limited to `monitoring` and the CI runner namespace; Redis ingress limited to orchestrator pods
- [x] Redis password; NetworkPolicy isolation
- [x] Orchestrator Role bound per namespace instead of cluster-wide
- [x] Pod Security `restricted` on `production`, `staging`, `canary` and `orchestrator`
- [x] No `admin123` or committed secrets; Terraform passwords are generated
- [x] Third-party GitHub Actions pinned by SHA; Dependabot configured; `pip-audit`, Trivy and Bandit block
- [ ] Run `scripts/test-network-policies.sh` on your cluster (Calico or Cilium) and narrow the API server egress
- [ ] `GITHUB_TOKEN` is a fine-grained token, scoped to one repository, Actions read/write only, rotated regularly
- [ ] `KUBE_CONFIG` is built from the `ci-deployer` ServiceAccount, not an admin kubeconfig
- [ ] Persistent volume for Redis, or a managed Redis with `REDIS_TLS=true`
- [ ] Terraform state in an encrypted backend
- [ ] Images pinned by digest, signed, with an SBOM (FEAT-14)
- [ ] Audit logs shipped to Loki or your SIEM with an access policy and retention that fits your needs
- [ ] Alertmanager wired to a receiver; runbooks written for `WebhookAuthenticationFailures`

## Secure Operation

- **Never commit** `.env` files, kubeconfigs, `terraform.tfstate`, or tokens. `scripts/create-secrets.sh --compose` writes a `0600` `.env` that `.gitignore` already excludes.
- **Rotate** the GitHub token and the secrets above on a schedule and after any suspected exposure:

  ```bash
  kubectl create secret generic github-token --from-literal=token="$NEW_TOKEN" \
    -n orchestrator --dry-run=client -o yaml | kubectl apply -f -
  kubectl rollout restart deployment/orchestrator -n orchestrator
  ```

- **Watch** `WebhookAuthenticationFailures` and the audit events `rollback_initiated` and `manual_intervention_required` (see [Monitoring](../usage/monitoring.md)). A burst of rejected webhooks after a rotation means the pipeline still holds the old secret; unexpected `POST /rollback` activity warrants investigation.
- **Scan locally** before opening a PR: `bandit -r orchestrator/src sample-app/src`, `pip-audit -r orchestrator/requirements.txt`, and `trivy image sample-app:<tag>`.

## Reporting a Vulnerability

Please **do not** open a public issue for security problems.

1. Use GitHub's private reporting: on the repository, open **Security, Report a vulnerability** (available when private vulnerability reporting is enabled for the repository).
2. If that option is not available, contact the maintainer, [@othaime-en](https://github.com/othaime-en), privately using the contact details on their GitHub profile.

Include a description, affected component and commit, reproduction steps or a proof of concept, and your assessment of impact. You can expect an acknowledgement, and the maintainer will work with you on a fix and coordinated disclosure. As a small volunteer project, MANA cannot offer fixed response-time guarantees or a bug bounty.

### Supported versions

Only the latest commit on `main` is supported; no tagged releases exist yet.

## Related Pages

- [Architecture Overview: Security Architecture](../architecture/overview.md#security-architecture)
- [Orchestrator Configuration](../configuration/orchestrator.md)
- [Monitoring](../usage/monitoring.md)
