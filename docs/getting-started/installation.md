# Installation Methods

MANA can be installed using several methods depending on your preferences and environment.

## Helm Chart Installation (Recommended)

The Helm chart is the recommended installation method as it provides easy configuration and upgrades.

### Prerequisites

- Helm 3.x installed
- Access to a Kubernetes cluster
- `kubectl` configured to access your cluster

### Adding the Repository

If you're using the official chart repository:

```bash
helm repo add mana https://othaime-en.github.io/mana/charts
helm repo update
```

### Installation Command

```bash
helm install mana mana/mana \
  --namespace mana --create-namespace \
  --set github.token=$GITHUB_TOKEN \
  --set github.repo="your-org/your-repo" \
  --set redis.enabled=true \
  --set autoscaling.enabled=true
```

### Common Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `github.token` | GitHub token for API access (required for retry functionality) | `""` |
| `github.repo` | GitHub repository in format "owner/repo" (required for retry functionality) | `""` |
| `redis.enabled` | Whether to deploy Redis as part of the chart | `true` |
| `redis.external.host` | Host for external Redis (if not using deployed Redis) | `""` |
| `autoscaling.enabled` | Enable HPA for the orchestrator | `false` |
| `replicaCount` | Number of orchestrator replicas | `1` |
| `image.tag` | Docker image tag | `"latest"` |
| `resources.requests.memory` | Memory request | `"256Mi"` |
| `resources.requests.cpu` | CPU request | `"100m"` |
| `resources.limits.memory` | Memory limit | `"512Mi"` |
| `resources.limits.cpu` | CPU limit | `"500m"` |

### Custom Values File

Create a `values.yaml` file with your configuration:

```yaml
replicaCount: 3

github:
  token: ""  # Set via Kubernetes secret in production
  repo: "your-org/your-repo"

redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10

resources:
  requests:
    memory: "512Mi"
    cpu: "250m"
  limits:
    memory: "1Gi"
    cpu: "500m"
```

Then install with:

```bash
helm install mana ./orchestrator/charts/mana \
  --namespace mana --create-namespace \
  -f values.yaml
```

## Manual Manifest Installation

If you prefer not to use Helm, you can apply the Kubernetes manifests directly.

### Prerequisites

- `kubectl` access to your cluster
- A running Redis instance (MANA requires Redis for state persistence)

### Steps

1. **Deploy Redis** (if not using an external instance):

```bash
kubectl apply -f orchestrator/k8s/redis.yaml
```

2. **Deploy the MANA orchestrator**:

```bash
kubectl apply -f orchestrator/k8s/deployment.yaml
```

3. **Verify deployment**:

```bash
kubectl -n orchestrator get pods
kubectl -n orchestrator get svc
```

### Customizing Manifests

When applying manifests directly, you'll need to edit the YAML files to set:
- Image tags
- Resource requests/limits
- Environment variables (GitHub token, Redis connection info, etc.)
- Replica counts

## Post-Installation Steps

After installation, regardless of method:

1. **Verify the installation**:

```bash
# Check that pods are running
kubectl -n mana get pods

# Check services
kubectl -n mana get svc

# Test the API
kubectl -n mana port-forward svc/mana-orchestrator 8000:8000 &
curl http://localhost:8000/health
```

2. **Configure your applications** to work with MANA:
   - Ensure they have proper version labels
   - Ensure they expose health endpoints
   - Ensure they're in namespaces MANA is configured to monitor

3. **Set up CI/CD integration**:
   - Configure your GitHub Actions workflows to send webhooks to MANA
   - Ensure the webhook includes the `workflow_run_id` for retry functionality

## Upgrading MANA

### Using Helm

```bash
# Update repository
helm repo update

# Upgrade release
helm upgrade mana mana/mana \
  --namespace mana \
  -f your-values.yaml
```

### Using Manual Manifests

1. Download the latest manifests from the repository
2. Apply the updated manifests:
   ```bash
   kubectl apply -f orchestrator/k8s/
   ```
3. Verify the upgrade worked correctly

## Uninstallation

### Using Helm

```bash
helm uninstall mana --namespace mana
```

### Using Manual Manifests

```bash
# Delete in reverse order of creation to avoid dependencies
kubectl delete -f orchestrator/k8s/deployment.yaml
kubectl delete -f orchestrator/k8s/redis.yaml
```

## Troubleshooting Installation

### Common Issues

#### ImagePullBackOff or ErrImagePull

- Verify the image name and tag are correct
- Check if your cluster can access the container registry
- Ensure any required image pull secrets are configured

#### CrashLoopBackOff

- Check the pod logs: `kubectl -n mana logs <pod-name>`
- Common causes:
  - Missing or incorrect GitHub token
  - Redis connection issues
  - Kubernetes RBAC permissions
  - Invalid configuration values

#### Service Unavailable

- Check if the service is exposed correctly
- Verify port forwarding is working
- Check if the application is listening on the correct port

### Getting Logs

```bash
# Get logs for a specific pod
kubectl -n mana logs <pod-name>

# Follow logs in real-time
kubectl -n mana logs -f <pod-name>

# Get logs for all orchestrator pods
kubectl -n mana logs -l app=mana-orchestrator
```

### Debugging Tips

1. **Check Events**: `kubectl -n mana get events --sort-by='.metadata.creationTimestamp'`
2. **Describe Resources**: `kubectl -n mana describe pod <pod-name>`
3. **Check ConfigMaps and Secrets**: Ensure they're correctly mounted
4. **Verify Network Policies**: Ensure the orchestrator can reach Redis and the Kubernetes API server