# Quick Start Guide

This guide provides a fast way to get MANA running in a local or test environment using kind (Kubernetes in Docker) or minikube.

## Option 1: Using kind (Kubernetes in Docker)

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/docs/intro/install/)

### Steps

1. **Create a kind cluster**:

```bash
kind create cluster --name mana-demo
```

2. **Verify the cluster is ready**:

```bash
kubectl cluster-info
kubectl get nodes
```

3. **Install MANA using Helm**:

```bash
# Clone the repository (if you haven't already)
git clone https://github.com/othaime-en/mana.git
cd mana

# Install MANA with default settings
helm install mana ./orchestrator/charts/mana \
  --namespace mana --create-namespace
```

4. **Verify installation**:

```bash
# Check pods
kubectl -n mana get pods

# Check services
kubectl -n mana get svc

# Port forward to access the API
kubectl -n mana port-forward svc/mana-orchestrator 8000:8000 &
```

5. **Test the API**:

```bash
# In another terminal
curl http://localhost:8000/
# Should return service information

curl http://localhost:8000/health
# Should return health status
```

### Deploy a Sample Application

Let's deploy a simple application to test MANA's self-healing capabilities:

1. **Create a namespace for our sample app**:

```bash
kubectl create namespace demo
```

2. **Deploy a simple HTTP server** (we'll use a basic nginx deployment for demonstration):

```bash
kubectl create deployment nginx-demo --image=nginx -n demo
kubectl expose deployment nginx-demo --port=80 --target-port=80 -n demo
```

3. **Add version label** (required for MANA's rollback functionality):

```bash
kubectl label deployment nginx-demo app.kubernetes.io/version=1.0.0 -n demo
```

4. **Add a simple health endpoint** (for demonstration, we'll create a ConfigMap with a health endpoint):

```bash
# Create a simple health-checking nginx config
cat > health-nginx.conf <<'EOF'
events { worker_connections 1024; }

http {
    server {
        listen 80;
        location /health {
            return 200 '{"status":"healthy"}';
            add_header Content-Type application/json;
        }
        location / {
            return 200 'Welcome to nginx!';
            add_header Content-Type text/plain;
        }
    }
}
EOF

# Create ConfigMap
kubectl create configmap nginx-health-conf --from-file=health-nginx.conf -n demo

# Update deployment to use our custom config
kubectl set volume deployment/nginx-demo \
  --add --name=config-volume \
  --type=configmap \
  --configmap-name=nginx-health-conf \
  -n demo

kubectl set volume deployment/nginx-demo \
  --add --name=health-volume \
  --type=configMap \
  --configmap-name=nginx-health-conf \
  --mount-path=/etc/nginx/conf.d \
  -n demo

# Update the container command to use our config
kubectl set image deployment/nginx-demo nginx=nginx:latest -n demo
```

5. **Verify the deployment**:

```bash
kubectl -n demo get pods
kubectl -n demo get svc
```

6. **Test MANA's monitoring**:

```bash
# Check if MANA can see our deployment
curl http://localhost:8000/deployments/demo/nginx-demo
```

## Option 2: Using minikube

### Prerequisites

- [minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/docs/intro/install/)

### Steps

1. **Start minikube**:

```bash
minikube start
```

2. **Enable the ingress addon** (optional but recommended for testing):

```bash
minikube addons enable ingress
```

3. **Install MANA**:

```bash
git clone https://github.com/othaime-en/mana.git
cd mana

helm install mana ./orchestrator/charts/mana \
  --namespace mana --create-namespace
```

4. **Verify and test** (same as kind steps above).

## Option 3: Using Docker Compose (for development only)

For development and testing purposes, you can use Docker Compose to run MANA and its dependencies locally.

### Prerequisites

- [Docker Compose](https://docs.docker.com/compose/install/)
- [git](https://git-scm.com/downloads)

### Steps

1. **Clone the repository**:

```bash
git clone https://github.com/othaime-en/mana.git
cd mana
```

2. **Create a docker-compose.yml file**:

```yaml
version: '3.8'

services:
  redis:
    image: redis:8-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  orchestrator:
    build: ./orchestrator
    ports:
      - "8000:8000"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - MAX_RETRIES=3
      - ROLLBACK_THRESHOLD=2
      - INITIAL_BACKOFF=10.0
      - MAX_BACKOFF=300.0
      - BACKOFF_MULTIPLIER=2.0
      - HEALTH_CHECK_TIMEOUT=5
      - HEALTH_CHECK_PORT=5000
      - HEALTH_CHECK_PATH=/health
      - LOG_LEVEL=INFO
      - ENABLE_AUDIT_LOGGING=true
    depends_on:
      - redis

volumes:
  redis_data:
```

3. **Start the services**:

```bash
docker-compose up -d
```

4. **Verify the services are running**:

```bash
docker-compose ps
```

5. **Test the API**:

```bash
curl http://localhost:8000/
curl http://localhost:8000/health
```

## Testing Self-Healing Capabilities

Once you have MANA running with a sample application, you can test its self-healing capabilities:

### Simulating a Failure

1. **Deploy an application that will fail health checks**:

```bash
# Create a deployment that fails health checks
cat > failing-app.yaml <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: failing-app
  labels:
    app: failing-app
    version: "1.0.0"
spec:
  replicas: 1
  selector:
    matchLabels:
      app: failing-app
  template:
    metadata:
      labels:
        app: failing-app
        version: "1.0.0"
    spec:
      containers:
      - name: failing-app
        image: nginx:latest
        ports:
        - containerPort: 80
        livenessProbe:
          httpGet:
            path: /health
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 5
        # This container will fail health checks because it doesn't have a /health endpoint
EOF

kubectl apply -f failing-app.yaml -n demo
```

2. **Watch MANA respond**:

```bash
# Monitor MANA logs
kubectl -n mana logs -f -l app=mana-orchestrator

# In another terminal, watch the deployment
kubectl -n demo get pods -w
```

You should see:
1. MANA detecting the unhealthy deployment
2. MANA scheduling a retry (if GitHub integration is configured)
3. If retries are exhausted, MANA initiating a rollback

## Cleaning Up

When you're done with your demo:

### For kind:
```bash
kind delete cluster --name mana-demo
```

### For minikube:
```bash
minikube stop
# or
minikube delete
```

### For Docker Compose:
```bash
docker-compose down
```

## Next Steps

After completing this quick start guide:

1. [Read the Architecture Overview](architecture/overview.md) to understand how MANA works internally
2. [Learn about Configuration Options](configuration/overview.md) to tailor MANA to your needs
3. [Explore CI/CD Integration](usage/cicd-integration.md) to connect MANA with your pipeline
4. [Review the API Reference](reference/api.md) for programmatic interaction