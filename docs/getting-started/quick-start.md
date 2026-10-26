# Quick Start Guide

This walkthrough runs MANA on a local Kind (or Minikube) cluster, deploys the bundled sample app, and then simulates a failing deployment so you can watch the orchestrator **alert, retry, and finally roll back**. It takes about 10 minutes and needs no GitHub account.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/) (or [minikube](https://minikube.sigs.k8s.io/docs/start/))
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- `curl` and [`jq`](https://jqlang.github.io/jq/)

## 1. Create a Cluster and Build the Images

```bash
git clone https://github.com/othaime-en/mana.git
cd mana

kind create cluster --name mana-demo
kubectl cluster-info --context kind-mana-demo

docker build -t orchestrator:latest orchestrator/
docker build -t sample-app:1.0.0 sample-app/

kind load docker-image orchestrator:latest sample-app:1.0.0 --name mana-demo
```

??? note "Using Minikube instead"
`bash
    minikube start
    docker build -t orchestrator:latest orchestrator/
    docker build -t sample-app:1.0.0 sample-app/
    minikube image load orchestrator:latest
    minikube image load sample-app:1.0.0
    `
Everything else in this guide is the same.

## 2. Install MANA and the Sample App

```bash
kubectl create namespace orchestrator
kubectl create namespace production

# MANA needs this secret to start. The value only matters for real GitHub retries.
kubectl create secret generic github-token \
  --from-literal=token=placeholder -n orchestrator

kubectl apply -f orchestrator/k8s/redis.yaml
kubectl apply -f orchestrator/k8s/deployment.yaml
kubectl apply -f sample-app/k8s/ -n production

kubectl rollout status deployment/orchestrator -n orchestrator --timeout=180s
kubectl rollout status deployment/sample-app   -n production   --timeout=180s
```

The orchestrator waits 60 seconds before its first probe, so allow a minute or two. If the sample app's Ingress or HPA warn about a missing controller or metrics server, you can ignore them for this demo.

## 3. Check That It Works

```bash
kubectl -n orchestrator port-forward svc/orchestrator-service 8000:8000 &
export MANA=http://localhost:8000

curl -s $MANA/health | jq
```

```json
{
  "status": "healthy",
  "redis": "healthy",
  "kubernetes": "healthy",
  "github_integration": true
}
```

(`github_integration` is `true` because the manifest sets both `GITHUB_TOKEN` and `GITHUB_REPO`. The token is a placeholder, so any real re-run request to GitHub will be rejected; that is expected in this demo. See [Orchestrator Configuration](../configuration/orchestrator.md#github-integration).)

Open <http://localhost:8000/docs> for interactive API docs, and check the sample app:

```bash
kubectl -n production get pods -l app=sample-app
kubectl -n production port-forward svc/sample-app-service 8080:80 &
curl -s localhost:8080/health | jq
```

## 4. Teach MANA a Known-Good Version

MANA rolls back to the **last version that reported success**. In a real pipeline the deploy workflow sends this webhook for you; here you send it by hand:

```bash
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' -d '{
  "deployment_id": "demo-good",
  "namespace": "production",
  "app_name": "sample-app",
  "version": "1.0.0",
  "status": "success"
}' | jq
```

```json
{
  "status": "success",
  "deployment_id": "demo-good",
  "message": "Deployment successful - version saved for future rollbacks"
}
```

Confirm it was stored:

```bash
kubectl -n orchestrator exec deploy/redis -- redis-cli get last_successful:production:sample-app
# 1.0.0
```

## 5. Break the Deployment

Roll out an image that does not exist, as a bad release would:

```bash
kubectl -n production set image deployment/sample-app sample-app=sample-app:1.1.0-broken
kubectl -n production get pods -l app=sample-app
```

Some pods will show `ErrImagePull` or `ImagePullBackOff`. The rollout is stuck on the new version.

## 6. Report the Failure and Watch MANA Decide

A pipeline would send a `failed` webhook when its rollout times out. Send it for the same `deployment_id` four times, waiting a moment between calls, and watch the decision change. (Run the log command in another terminal.)

```bash
kubectl -n orchestrator logs -l app=orchestrator -f --tail=5
```

```bash
for i in 1 2 3 4; do
  echo "--- failure #$i"
  curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' -d '{
    "deployment_id": "demo-bad",
    "namespace": "production",
    "app_name": "sample-app",
    "version": "1.1.0-broken",
    "status": "failed",
    "failure_type": "deployment_failure",
    "metadata": {"workflow_run_id": "1"}
  }' | jq -c .action_taken
  sleep 3
done
```

Expected output:

```text
--- failure #1
{"action":"alert","message":"Cannot retry - missing workflow_run_id. Manual intervention required."}
--- failure #2
{"action":"retry","retry_count":2,"backoff_seconds":20.0,"workflow_run_id":"1","message":"Retry 2/3 scheduled in 20.0s"}
--- failure #3
{"action":"retry","retry_count":3,"backoff_seconds":40.0,"workflow_run_id":"1","message":"Retry 3/3 scheduled in 40.0s"}
--- failure #4
{"action":"rollback","previous_version":"1.0.0","message":"Rolled back to version 1.0.0"}
```

What happened:

| Failure | Decision   | Why                                                                                                                                                                                                                                                                                    |
| ------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1       | `alert`    | The first failure for a new deployment ID has no stored `workflow_run_id` yet (a known limitation; see [Data Flow](../architecture/data-flow.md#known-behaviours-and-limitations))                                                                                                     |
| 2, 3    | `retry`    | Within `MAX_RETRIES=3`; a workflow re-run is scheduled after 20 s and 40 s of backoff. Because the token is a placeholder, GitHub rejects the re-run request (an error such as `Failed to trigger re-run: 401` appears in the logs after the backoff), which is harmless for this demo |
| 4       | `rollback` | `retry_count` (4) now exceeds `MAX_RETRIES`, a previous version (`1.0.0`) is known, and the failed version is the one running                                                                                                                                                                   |

The fourth call takes a few seconds: the orchestrator patches the Deployment back to `sample-app:1.0.0`, waits for all replicas to be ready, and calls each pod's `/health` endpoint before answering.

## 7. Verify the Recovery

```bash
kubectl -n production get deployment sample-app \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# sample-app:1.0.0

kubectl -n production get pods -l app=sample-app

curl -s $MANA/deployment/demo-bad | jq
```

```json
{
  "deployment_id": "demo-bad",
  "namespace": "production",
  "app_name": "sample-app",
  "version": "1.1.0-broken",
  "status": "rolled_back",
  "retry_count": 4,
  "failure_type": "deployment_failure",
  "previous_version": "1.0.0",
  "timestamp": 1760000123.45,
  "metadata": { "workflow_run_id": "1" }
}
```

See the audit trail for the whole story:

```bash
kubectl -n orchestrator logs -l app=orchestrator | grep "AUDIT:"
```

You should see `deployment_received`, `failure_detected`, `retry_initiated`, `rollback_initiated`, `health_check_passed` and `rollback_completed` entries.

You can also trigger the same rollback yourself at any time:

```bash
curl -s -X POST $MANA/rollback -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","target_version":"1.0.0"}' | jq
```

## Other Ways to Run Locally

### Docker Compose

`docker-compose.yml` in the repository starts Redis, the sample app, the orchestrator and the monitoring stack (`make dev-start`). The orchestrator still needs a Kubernetes API, so this works only if the mounted kubeconfig is readable by the container's non-root user and points at an address reachable from Docker. See the notes in [Development Setup](../contributing/development-setup.md#4-docker-compose-stack). For a first try, the Kind route above is simpler.

### Running the orchestrator on your machine

Run Redis in Docker and the orchestrator with `uvicorn` against your local kubeconfig, as described in [Development Setup](../contributing/development-setup.md#3-run-services-locally-without-containers).

## Clean Up

```bash
kill %1 %2 2>/dev/null          # stop port-forwards started in this shell
kind delete cluster --name mana-demo
# or: minikube delete
```

## Where to Go Next

- [Installation](installation.md): installing on a real cluster
- [CI/CD Integration](../usage/cicd-integration.md): have your pipeline send these webhooks automatically
- [Configuration](../configuration/overview.md): tune retries, backoff and health checks
- [Basic Usage](../usage/basic-usage.md): day-to-day operation
- [Architecture Overview](../architecture/overview.md): how it all fits together
