# Getting Started with MANA

This guide will help you get MANA up and running in your environment.

## Prerequisites

Before installing MANA, ensure you have:

- **Kubernetes Cluster**: v1.22+ (EKS, GKE, AKS, or self-hosted)
- **kubectl**: Configured to access your cluster
- **Helm 3.x**: For easy installation (though manual manifests are available)
- **GitHub Repository**: With GitHub Actions workflows that deploy to Kubernetes
- **Target Applications**: Deployed to Kubernetes with:
  - Version labels (e.g., `app.kubernetes.io/version`)
  - Health endpoints (`/health` returning JSON with `{status: "healthy"}`)
  - Proper labels for selection (e.g., `app=your-app-name`)

## Installation

### Option 1: Helm Chart (Recommended)

```bash
# Add the MANA repository (if hosting separately) or use local path
helm repo add mana https://othaime-en.github.io/mana/charts
helm repo update

# Install MANA
helm install mana mana/mana \
  --namespace mana --create-namespace \
  --set github.token=$GITHUB_TOKEN \
  --set github.repo="your-org/your-repo"
```

### Option 2: Local Helm Chart

```bash
# Clone the repository
git clone https://github.com/othaime-en/mana.git
cd mana

# Install using local chart
helm install mana ./orchestrator/charts/mana \
  --namespace mana --create-namespace \
  --set github.token=$GITHUB_TOKEN \
  --set github.repo="your-org/your-repo"
```

### Option 3: Manual Manifests

```bash
# Apply the Kubernetes manifests
kubectl apply -f orchestrator/k8s/
```

## Quick Start

Here's a quick way to get MANA running with default settings:

```bash
# Clone the repository
git clone https://github.com/othaime-en/mana.git
cd mana

# Install MANA with default values (for testing only)
helm install mana ./orchestrator/charts/mana \
  --namespace mana --create-namespace

# Verify installation
kubectl -n mana get pods
kubectl -n mana port-forward svc/mana-orchestrator 8000:8000

# Access the API
curl http://localhost:8000/
```

## Post-Installation Verification

After installation, verify that MANA is running correctly:

```bash
# Check pod status
kubectl -n mana get pods

# Check service status
kubectl -n mana get svc

# Check the API health endpoint
curl http://<cluster-ip>:<node-port>/health
# Should return: {"status":"healthy","redis":"healthy","kubernetes":"healthy",...}
```

## Next Steps

Once MANA is installed:

1. [Configure MANA](configuration/overview.md) for your environment
2. [Integrate with your CI/CD pipeline](usage/cicd-integration.md)
3. [Deploy your first application](usage/basic-usage.md)
4. [Set up monitoring](usage/monitoring.md)

## Need Help?

If you encounter issues:
- Check the [Troubleshooting Guide](usage/troubleshooting.md)
- Review the [FAQ](usage/troubleshooting.md#frequently-asked-questions)
- Search existing [GitHub Issues](https://github.com/othaime-en/mana/issues)
- Open a new issue if needed