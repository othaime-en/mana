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

| Area                               | Implementation                                                                                                      | Where                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Non-root containers                | Images create UID 1000 users; manifests set `runAsNonRoot: true` and `runAsUser: 1000`                              | Dockerfiles, `*/k8s/deployment.yaml`                                     |
| Reduced privileges                 | `allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]` on app and orchestrator pods                       | `*/k8s/deployment.yaml`                                                  |
| Resource limits                    | Requests and limits on every workload; production namespace quota and LimitRange                                    | manifests, Terraform `security` module                                   |
| Least-privilege API access         | The orchestrator role is limited to Deployments, ReplicaSets, Pods and Events; it cannot read Secrets or ConfigMaps | `orchestrator-role`                                                      |
| Network segmentation               | NetworkPolicies for the sample app and orchestrator namespaces                                                      | `kubernetes/security/network-policies.yaml`, Terraform `security` module |
| Secret handling                    | GitHub token read from a Kubernetes Secret (`github-token`); CI secrets stored in GitHub Actions secrets            | `orchestrator/k8s/deployment.yaml`, workflows                            |
| Input validation                   | Pydantic models validate webhook and API payloads; configuration is range-checked at startup                        | `api.py`, `config.py`                                                    |
| Auditability                       | Structured audit events for every recovery action                                                                   | `utils/audit.py`                                                         |
| Supply-chain scanning              | Weekly and per-PR Snyk (dependencies), Trivy (container image) and Bandit (Python code) scans                       | `security-scan.yml`                                                      |
| Minimal blast radius of CI deploys | Environments (`staging`, `production`) so approvals can be required                                                 | `deploy-environment.yml`                                                 |

## Known Gaps

### 1. No authentication or authorization on the API

`/webhook/deployment`, `/rollback`, `/health-check` and the read endpoints accept any request. There is no shared secret, signature check or token. The Terraform module creates `webhook-token` and `api-key` entries in a Secret, but both hold the placeholder `change-me-in-production` and nothing reads them.

**Mitigations until built in:**

- Keep the Service as `ClusterIP`; reach it from CI through a self-hosted runner inside the network, or through an authenticating reverse proxy.
- Restrict ingress to the orchestrator pods with a NetworkPolicy that allows only the proxy or runner namespace. The shipped policy allows ingress on port 8000 from **all namespaces** (`namespaceSelector: {}`); tighten it.
- Do not publish the orchestrator URL publicly.

### 2. Redis has no authentication or TLS

The orchestrator connects with host and port only. Redis in the manifests has no password, and the Terraform `redis_password` variable only sets `--requirepass` on the server; the client cannot supply it. Isolate Redis with a NetworkPolicy so only orchestrator pods can reach it, or extend the client to read a password and TLS settings.

Redis in `orchestrator/k8s/redis.yaml` uses an `emptyDir`, so state is lost on rescheduling.

### 3. Cluster-wide write access

`orchestrator-role` is a `ClusterRole` bound cluster-wide, with `update` and `patch` on Deployments in every namespace. Limit it by replacing the ClusterRoleBinding with RoleBindings in only the namespaces MANA manages (`production`, `staging`, `canary`).

### 4. Default credentials

- Grafana's admin password is `admin123` in `terraform/modules/monitoring/main.tf`, the Helm values, `docker-compose.yml`, and `scripts/deploy.sh` output. Grafana's service type is `LoadBalancer`, which may expose it externally.
- Replace these with a Secret-backed password (`grafana.admin.existingSecret`) and SSO before exposing Grafana.

### 5. Security manifests that do not apply on vanilla Kubernetes

`kubernetes/security/network-policies.yaml` also contains a `PodSecurityPolicy` (removed from Kubernetes in 1.25) and an OpenShift `SecurityContextConstraints`. Applying the whole file to a current cluster fails on those objects. Use Pod Security Admission labels instead (`pod-security.kubernetes.io/enforce: restricted` on the application namespaces), and apply only the NetworkPolicy, quota and LimitRange objects. The file's example Secret also holds placeholder values; do not apply it as is.

### 6. NetworkPolicies may block MANA's own traffic

Before enabling the policies, verify that they allow what the orchestrator needs:

- The orchestrator calls pod IPs in the application namespace directly for health checks. The sample-app policy admits ingress only from `ingress-nginx` and `monitoring` namespaces, so the orchestrator's calls would be dropped.
- The orchestrator policy allows egress only to Redis and to `kube-system` on 443. It does not allow the Kubernetes API server address or `api.github.com:443`, which the orchestrator needs.
- Policies select namespaces by the label `name=<namespace>`, which Terraform sets. Namespaces created otherwise need the label.

Test with `kubectl exec` into the orchestrator pod (`curl` to a pod IP and to `https://api.github.com`) after applying policies.

### 7. Weak default container posture

`readOnlyRootFilesystem` is `false` on the sample app (set deliberately for startup behaviour) and unset on the orchestrator. Images are referenced by mutable tags (`latest`, `1.0.0`) rather than digests, and CI actions such as Trivy and Snyk are pinned to `@master`. For production pin images by digest and actions by version or commit SHA.

### 8. Secrets in CI

`KUBE_CONFIG` is typically an admin kubeconfig. Create a dedicated ServiceAccount limited to `get/patch` on Deployments (and `get/list` on Pods) in the target namespaces, and generate the kubeconfig from it. Prefer short-lived credentials (OIDC federation to your cloud) over long-lived kubeconfigs, and scope `GHCR_PAT` to `write:packages` only.

### 9. Forgery and replay

Without signatures, a captured webhook can be replayed and the same event delivered twice acts twice. Two orchestrator replicas have no leader election. Add HMAC signing (for example `X-Hub-Signature-256`-style) and idempotency keys when authentication is implemented.

## Hardening Checklist

- [ ] Orchestrator reachable only from your CI runners or an authenticating proxy
- [ ] NetworkPolicy on the orchestrator ingress limited to those sources; egress verified for Redis, the Kubernetes API and `api.github.com`
- [ ] Redis isolated by NetworkPolicy, persistent volume configured, authentication added if you extend the client
- [ ] `orchestrator-role` bound per namespace instead of cluster-wide
- [ ] `GITHUB_TOKEN` is a fine-grained token, scoped to one repository, Actions read/write only, rotated regularly
- [ ] `KUBE_CONFIG` belongs to a least-privilege ServiceAccount
- [ ] Grafana admin password changed; Grafana not publicly exposed; Alertmanager secrets mounted from Kubernetes Secrets
- [ ] Pod Security Admission set to `restricted` on `production`, `staging` and `orchestrator`
- [ ] Images pinned by digest; Dependabot or Renovate enabled; Trivy fails the build on `CRITICAL`
- [ ] Third-party GitHub Actions pinned to versions or SHAs
- [ ] Secrets managed with Sealed Secrets, External Secrets Operator or Vault rather than committed manifests
- [ ] Audit logs shipped to Loki or your SIEM with an access policy and retention that fits your compliance needs
- [ ] Webhook authentication implemented (see [Known Gaps](#1-no-authentication-or-authorization-on-the-api))

## Secure Operation

- **Never commit** `.env` files, kubeconfigs, `terraform.tfstate`, or tokens. `orchestrator/.env.example` contains placeholders only.
- **Rotate** the GitHub token and any webhook secrets on a schedule and after any suspected exposure:

  ```bash
  kubectl create secret generic github-token --from-literal=token="$NEW_TOKEN" \
    -n orchestrator --dry-run=client -o yaml | kubectl apply -f -
  kubectl rollout restart deployment/orchestrator -n orchestrator
  ```

- **Review** audit events regularly, especially `rollback_initiated` and `manual_intervention_required` (see [Monitoring](../usage/monitoring.md)). Unexpected `POST /rollback` activity warrants investigation.
- **Scan locally** before opening a PR: `bandit -r orchestrator/src sample-app/src` and `trivy image sample-app:<tag>`.

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
