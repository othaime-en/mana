# Installation

MANA is installed from the Kubernetes manifests in `orchestrator/k8s/` or with the Terraform modules in `terraform/`. There is no Helm chart for MANA itself (Helm is used only to install the monitoring stack and, in `scripts/deploy.sh`, an optional Redis).

For a guided local walkthrough see the [Quick Start](quick-start.md).

## What Gets Installed

| Resource                                                                                                     | Namespace      | Source                             |
| ------------------------------------------------------------------------------------------------------------ | -------------- | ---------------------------------- |
| Namespace `orchestrator`                                                                                     |                | `orchestrator/k8s/deployment.yaml` |
| Deployment `orchestrator` (2 replicas) and Service `orchestrator-service` (8000)                             | `orchestrator` | `orchestrator/k8s/deployment.yaml` |
| ServiceAccount `orchestrator-sa`, ClusterRole `orchestrator-role`, ClusterRoleBinding `orchestrator-binding` | cluster-wide   | `orchestrator/k8s/deployment.yaml` |
| Deployment `redis` (1 replica) and Service `redis-service` (6379)                                            | `orchestrator` | `orchestrator/k8s/redis.yaml`      |
| Secret `github-token`                                                                                        | `orchestrator` | **you create it**                  |

## Prerequisites

- A Kubernetes cluster, v1.23+, and `kubectl` access with rights to create namespaces and cluster-scoped RBAC
- Docker, to build the orchestrator image (or a registry that already hosts it)
- A GitHub token, if you want automatic retries (see [Orchestrator Configuration](../configuration/orchestrator.md#github-integration))

## Method 1: Manual Manifests

### 1. Get the orchestrator image into the cluster

The manifest uses `image: orchestrator:latest` with `imagePullPolicy: IfNotPresent`, so the image must exist on the nodes or be pullable under that name.

=== "Kind"

    ```bash
    docker build -t orchestrator:latest orchestrator/
    kind load docker-image orchestrator:latest --name <cluster-name>
    ```

=== "Minikube"

    ```bash
    docker build -t orchestrator:latest orchestrator/
    minikube image load orchestrator:latest
    ```

=== "Registry (EKS, GKE, AKS, shared clusters)"

    ```bash
    docker build -t <registry>/orchestrator:<tag> orchestrator/
    docker push <registry>/orchestrator:<tag>
    # then edit orchestrator/k8s/deployment.yaml: image: <registry>/orchestrator:<tag>
    ```

    The CI workflow also publishes `ghcr.io/<owner>/<repo>/orchestrator:<version>` and `:latest` on pushes. GHCR packages are private by default, so either make the package public or add an `imagePullSecret` to the ServiceAccount or pod spec.

### 2. Create namespaces and the GitHub secret

```bash
kubectl create namespace orchestrator

kubectl create secret generic github-token \
  --from-literal=token="$GITHUB_TOKEN" \
  -n orchestrator
```

The orchestrator Deployment reads `GITHUB_TOKEN` from this secret, so the pods will not start (`CreateContainerConfigError`) if it is missing. If you do not want retries, create the secret with any placeholder value; rollbacks do not need GitHub.

### 3. Set your repository

Edit `orchestrator/k8s/deployment.yaml`:

```yaml
- name: GITHUB_REPO
  value: "your-org/your-repo" # ships as othaime-en/mana
```

Review the other values in the same `env:` block (see [Configuration](../configuration/orchestrator.md)).

### 4. Apply

```bash
kubectl apply -f orchestrator/k8s/redis.yaml
kubectl apply -f orchestrator/k8s/deployment.yaml
kubectl rollout status deployment/orchestrator -n orchestrator --timeout=180s
```

(`kubectl apply -f orchestrator/k8s/ -n orchestrator` applies both files in one go.) The readiness and liveness probes wait 60 seconds before the first check, so allow a minute or two.

### 5. Deploy your target application

For a first test deploy the bundled sample app:

```bash
docker build -t sample-app:1.0.0 sample-app/       # and load it as in step 1
kubectl create namespace production
kubectl apply -f sample-app/k8s/ -n production
```

## Method 2: Terraform

```bash
cd terraform
terraform init
terraform plan  -var="environment=local" -var="enable_staging=false"
terraform apply -var="environment=local" -var="enable_staging=false"
```

This creates all namespaces (`production`, `staging`, `canary`, `orchestrator`, `monitoring`), the sample app, the orchestrator with Redis, NetworkPolicies and quotas, and (unless `enable_monitoring=false`) Prometheus, Grafana and Loki. Terraform expects the images to be available to the cluster as well; set `image_registry` if they live in a registry.

Differences from the manual manifests:

- Terraform sets only `PORT`, `REDIS_HOST`, `REDIS_PORT`, `MAX_RETRIES` and `LOG_LEVEL` on the orchestrator. Add `GITHUB_TOKEN` and `GITHUB_REPO` yourself to enable retries.
- Do not mix `kubectl apply` and Terraform on the same resources; pick one to avoid drift.
- State is local by default. For teams, switch `backend.tf` to the S3 and DynamoDB example it contains.

See the [Configuration Reference](../reference/configuration.md#terraform-variables) for all variables.

## Method 3: `scripts/deploy.sh`

Runs Terraform, builds and loads images, deploys Redis (via Helm), the orchestrator and the sample app, installs monitoring and runs smoke tests:

```bash
./scripts/deploy.sh --skip-monitoring
```

Read the notes in the [CLI Reference](../reference/cli.md#scriptsdeploysh) first; the script assumes Terraform, Helm and a local cluster, and it creates a Redis the orchestrator does not use by default.

## Using an External Redis

Point the orchestrator at your own Redis by changing `REDIS_HOST` and `REDIS_PORT`, and skip `redis.yaml`. The client has no password or TLS support yet, so use a network-isolated instance. The bundled Redis uses an `emptyDir`, so state is lost if its pod is rescheduled; that is acceptable for testing because deployment records expire after 24 hours anyway, but it also loses the `last_successful:*` rollback targets.

## Post-Installation Checks

```bash
# Pods and services
kubectl -n orchestrator get pods,svc

# API health (Redis + Kubernetes + GitHub integration flag)
kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000 &
curl -s localhost:8000/health | jq

# Effective configuration
curl -s localhost:8000/config | jq

# RBAC: can the orchestrator patch Deployments in your app namespace?
kubectl auth can-i patch deployments -n production \
  --as=system:serviceaccount:orchestrator:orchestrator-sa
```

Then:

1. Make sure your applications follow the [conventions](index.md#conventions-for-target-applications).
2. Set up [CI/CD integration](../usage/cicd-integration.md) so the pipeline sends webhooks.
3. Restrict who can reach the orchestrator (it has no authentication); see [Security](../about/security.md).

## Upgrading

```bash
git pull
docker build -t orchestrator:latest orchestrator/          # load or push as above
kubectl apply -f orchestrator/k8s/
kubectl rollout restart deployment/orchestrator -n orchestrator
kubectl rollout status deployment/orchestrator -n orchestrator
```

Restart the pods after any configuration change; settings are read only at startup. Deployment records in Redis expire after 24 hours and are not affected by an upgrade, and `last_successful:*` keys persist as long as Redis does.

## Uninstalling

```bash
kubectl delete -f orchestrator/k8s/redis.yaml
kubectl delete -f orchestrator/k8s/deployment.yaml
kubectl delete clusterrole orchestrator-role --ignore-not-found
kubectl delete clusterrolebinding orchestrator-binding --ignore-not-found
```

Delete `redis.yaml` first: `deployment.yaml` also defines the `orchestrator` namespace, so removing it deletes everything inside, including the `github-token` secret. It also removes the ServiceAccount, ClusterRole and ClusterRoleBinding it defines; the explicit `clusterrole` commands are a safety net. With Terraform, run `terraform destroy` (this removes **all** modules, including your sample apps and monitoring).

## Troubleshooting Installation

| Symptom                                                                                      | Cause                                     | Fix                                                                                         |
| -------------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------- |
| `ImagePullBackOff` / `ErrImagePull`                                                          | `orchestrator:latest` is not on the nodes | Load the image (Kind/Minikube) or push to a registry and edit the image name                |
| `CreateContainerConfigError`                                                                 | Secret `github-token` missing             | `kubectl create secret generic github-token ... -n orchestrator`                            |
| `CrashLoopBackOff`, logs show `Cannot connect to Redis`                                      | Redis not running or wrong `REDIS_HOST`   | `kubectl get pods -n orchestrator -l app=redis`; host must be `redis-service` in-cluster    |
| `CrashLoopBackOff`, logs show a `ValueError` such as `max_backoff must be > initial_backoff` | Invalid configuration                     | Fix the value (see [validation errors](../configuration/orchestrator.md#validation-errors)) |
| Pods `Running` but not Ready for about a minute                                              | Probes have a 60 s initial delay          | Wait                                                                                        |
| `/health` returns `degraded`                                                                 | Redis or the Kubernetes API unreachable   | Check Redis pod, ServiceAccount and NetworkPolicies                                         |
| `kubectl auth can-i` returns `no`                                                            | ClusterRole or binding not applied        | Re-apply `deployment.yaml`                                                                  |

Useful commands:

```bash
kubectl -n orchestrator logs -l app=orchestrator --tail=100
kubectl -n orchestrator describe pod -l app=orchestrator
kubectl -n orchestrator get events --sort-by=.metadata.creationTimestamp
```

More in the [Troubleshooting Guide](../usage/troubleshooting.md).
