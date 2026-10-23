# Getting Started with MANA

This guide gets MANA running and explains what you need in place before it can heal anything. MANA is installed from plain Kubernetes manifests (or Terraform); there is no Helm chart.

## How MANA Fits Together

MANA is **event-driven**. Your CI/CD pipeline tells the orchestrator when a deployment starts, succeeds or fails, and the orchestrator reacts by retrying (through a GitHub Actions re-run) or rolling the Deployment back. It does not scan your cluster for new Deployments on its own.

You therefore need three things:

1. **The orchestrator and Redis** running in your cluster (this guide).
2. **A target application** that follows a few conventions (below).
3. **A pipeline that sends webhooks** ([CI/CD Integration](../usage/cicd-integration.md)).

## Prerequisites

| Requirement        | Details                                                                                                                                |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Kubernetes cluster | v1.23 or newer (the manifests use `autoscaling/v2` and `policy/v1`). Kind, Minikube, EKS, GKE and AKS all work                         |
| `kubectl`          | Configured for the cluster                                                                                                             |
| Docker             | To build the orchestrator image (the manifests reference `orchestrator:latest`, which is not pulled from a public registry by default) |
| GitHub repository  | Only needed for automatic retries; rollbacks work without it                                                                           |
| GitHub token       | Fine-grained token with _Actions: Read and write_ for retries (optional)                                                               |

Optional, for the full stack: Terraform 1.5+ and Helm 3 (used by `scripts/deploy.sh` and the Terraform monitoring module).

### Conventions for target applications

The orchestrator finds and verifies your workloads using these conventions:

- The **Deployment name**, the pod label **`app=<name>`**, and the **container name** are all the same (for example `sample-app`). Rollback edits the container whose name equals the Deployment name.
- Pods carry a **`version` label** equal to the image tag (used as a fallback source for the previous version).
- The application serves **`GET /health`** (path and port are configurable) and returns **HTTP 200 with JSON `{"status": "healthy"}`**.
- Images are tagged per release (not just `latest`) so a rollback has something to go back to.

The bundled `sample-app/` follows all of these.

## Installation

### Option 1: Local cluster with Kind (recommended for trying MANA)

The full walkthrough, including a demo of the retry and rollback decisions, is in the [Quick Start](quick-start.md). In short:

```bash
git clone https://github.com/othaime-en/mana.git && cd mana
kind create cluster --name mana-demo

docker build -t orchestrator:latest orchestrator/
docker build -t sample-app:1.0.0 sample-app/
kind load docker-image orchestrator:latest sample-app:1.0.0 --name mana-demo

kubectl create namespace production
kubectl create namespace orchestrator
kubectl create secret generic github-token --from-literal=token=placeholder -n orchestrator
kubectl apply -f orchestrator/k8s/ -n orchestrator
kubectl apply -f sample-app/k8s/ -n production
```

### Option 2: Existing cluster with manual manifests

See [Installation](installation.md) for image registry, secrets and RBAC details.

### Option 3: Terraform

`terraform/` provisions namespaces, the sample app, the orchestrator with Redis, security policies and the monitoring stack in one apply:

```bash
cd terraform
terraform init
terraform apply -var="environment=local" -var="enable_staging=false"
```

!!! note
The Terraform orchestrator module does not set `GITHUB_TOKEN` or `GITHUB_REPO`, so retries cannot trigger workflow re-runs until you add them to the Deployment.

### Option 4: One command

`./scripts/deploy.sh` runs Terraform, builds and loads images, installs Redis and the orchestrator, deploys the sample app, installs monitoring and runs smoke tests. See the [CLI Reference](../reference/cli.md#scriptsdeploysh) before using it, because it has several assumptions.

## Verify the Installation

```bash
kubectl -n orchestrator get pods
kubectl -n orchestrator get svc

kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000 &
curl -s http://localhost:8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "redis": "healthy",
  "kubernetes": "healthy",
  "github_integration": true
}
```

`github_integration` is `true` when both `GITHUB_TOKEN` and `GITHUB_REPO` are set (the shipped manifest sets both, so it reads `true` even with a placeholder token). It is `false` for deployments that omit them, such as the Terraform module as shipped. `status: degraded` means Redis or the Kubernetes API is unreachable; see [Troubleshooting](../usage/troubleshooting.md).

Interactive API documentation is served at <http://localhost:8000/docs>.

## Next Steps

1. [Try the Quick Start demo](quick-start.md) to watch a retry-then-rollback sequence.
2. [Configure MANA](../configuration/overview.md) for your environment.
3. [Integrate with your CI/CD pipeline](../usage/cicd-integration.md).
4. [Learn day-to-day usage](../usage/basic-usage.md).
5. [Set up monitoring](../usage/monitoring.md).

## Need Help?

- Check the [Troubleshooting Guide](../usage/troubleshooting.md) and its [FAQ](../usage/troubleshooting.md#frequently-asked-questions)
- Search [GitHub Issues](https://github.com/othaime-en/mana/issues)
- Open a new issue if needed
