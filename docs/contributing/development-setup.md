# Development Setup

How to get a working development environment for MANA, run the tests, and exercise the system at three levels of fidelity: unit tests with mocks, a Docker Compose stack, and a local Kubernetes cluster.

## Prerequisites

| Tool             | Version    | Needed for                  |
| ---------------- | ---------- | --------------------------- |
| Python           | 3.12+      | Orchestrator and sample app |
| Git              | any recent | Source control              |
| Docker           | 24+        | Images, Compose stack, Kind |
| kubectl          | 1.28+      | Cluster interaction         |
| Kind or Minikube | recent     | Local Kubernetes            |
| Terraform        | 1.5+       | Infrastructure modules      |
| Helm             | 3.x        | `scripts/deploy.sh` (Redis) |
| make             | any        | Shortcuts                   |
| jq, curl         | any        | Examples and scripts        |

You only need Python and Git for unit-test work.

## 1. Clone and Install

```bash
git clone https://github.com/othaime-en/mana.git
cd mana

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r orchestrator/requirements.txt -r sample-app/requirements.txt
pip install flake8 black isort pylint   # optional: linters used by `make lint`
```

Both `requirements.txt` files include the test tools (`pytest`, `pytest-cov`, and `pytest-asyncio` for the orchestrator), so no separate dev requirements file exists.

## 2. Run the Tests

Tests mock Redis, Kubernetes and GitHub, so no cluster is needed.

```bash
make test                                   # both suites
cd sample-app   && pytest tests/ -v --cov=src --cov-report=term-missing
cd orchestrator && pytest tests/ -v --cov=src --cov-report=term-missing
```

Current baseline (at the time of writing):

| Suite          | Result                  | Coverage                          |
| -------------- | ----------------------- | --------------------------------- |
| `sample-app`   | 35 passed               | n/a                               |
| `orchestrator` | 41 passed, **2 failed** | about 48% (`api.py` has no tests) |

The two failing tests, `test_handle_deployment_failure_retry_with_backoff` and `test_handle_deployment_failure_increasing_backoff`, fail because the retry path returns `alert` when `workflow_run_id` is not in the stored state. This is a real bug rather than a flaky test; see [Data Flow: Known Behaviours](../architecture/data-flow.md#known-behaviours-and-limitations). Fixing it is a good first contribution.

!!! note "Test isolation"
`orchestrator/src/api.py` creates the orchestrator at import time, which connects to Redis and loads a kubeconfig. Tests that import it must patch `redis.Redis` and the `kubernetes.config`/`client` calls (as in `tests/conftest.py`-style fixtures), or construct `SelfHealingOrchestrator(use_config=False)`.

## 3. Run Services Locally Without Containers

### Orchestrator

You need a Redis and a kubeconfig that points at any cluster (Kind is fine).

```bash
docker run -d --name mana-redis -p 6379:6379 redis:8-alpine
cd orchestrator
cp .env.example .env              # set GITHUB_TOKEN/GITHUB_REPO only if you want retries
set -a; source .env; set +a
uvicorn src.api:app --reload --port 8000
```

Open <http://localhost:8000/docs> for the interactive API. The orchestrator exits at startup if Redis or Kubernetes is unreachable.

### Sample app

```bash
cd sample-app
FAILURE_RATE=0.1 ENVIRONMENT=development python -m flask --app src.app run --port 5000
curl localhost:5000/health
```

## 4. Docker Compose Stack

```bash
make dev-start        # docker compose up -d
make dev-logs
make dev-stop
```

This starts Redis, the sample app, the orchestrator, Prometheus, Grafana (<http://localhost:3000>, `admin` / `admin123`), Loki and Promtail.

!!! warning "Known Compose issues to be aware of" - The orchestrator service mounts `~/.kube/config` to `/root/.kube/config`, but the image runs as the non-root user `orchestrator`, which cannot read `/root`. Mount the file to `/home/orchestrator/.kube/config` instead. - A kubeconfig for Kind or Minikube points at `127.0.0.1`, which inside the container is the container itself. Use a kubeconfig whose server address is reachable from Docker (for Kind: `kind get kubeconfig --internal` with the Kind network attached). - The orchestrator service's healthcheck uses `curl`, which the orchestrator image does not install, so Compose reports it unhealthy. Use `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"` or add `curl` to the image. - Prometheus in Compose expects an Alertmanager at `alertmanager:9093`, which Compose does not define; alert delivery will not work locally.

## 5. Local Kubernetes

```bash
kind create cluster --name self-healing-cicd        # or: minikube start
make build                                          # sample-app:<tag>, orchestrator:latest
make load-images                                    # kind/minikube image load
kubectl create namespace production
kubectl create namespace orchestrator
kubectl create secret generic github-token --from-literal=token=dummy -n orchestrator
kubectl apply -f orchestrator/k8s/ -n orchestrator
kubectl apply -f sample-app/k8s/ -n production
kubectl rollout status deployment/orchestrator -n orchestrator
make port-forward-orchestrator                      # in another terminal
```

Notes:

- `make setup` and `make setup-cluster` call scripts that are not in the repository yet; use the commands above.
- The sample-app Ingress requires an ingress controller; skip it with `kubectl apply -f sample-app/k8s/deployment.yaml` and delete the Ingress if you do not have one, or use port-forwarding.
- Alternatively `./scripts/deploy.sh --skip-monitoring` does the whole sequence (see the [CLI Reference](../reference/cli.md#scriptsdeploysh)); it needs Terraform and Helm.

### Try the self-healing loop

```bash
export MANA=http://localhost:8000
# 1. Record a known-good version
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' \
  -d '{"deployment_id":"d1","namespace":"production","app_name":"sample-app","version":"1.0.0","status":"success"}'
# 2. Report a failure for a newer version
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' \
  -d '{"deployment_id":"d2","namespace":"production","app_name":"sample-app","version":"1.1.0","status":"failed","failure_type":"health_check_failure","metadata":{"workflow_run_id":"1"}}'
# 3. Inspect
curl -s $MANA/deployment/d2 | jq
```

Repeat step 2 with the same `deployment_id` to walk through retries and, after the fourth failure, the rollback. Retries need `GITHUB_TOKEN`/`GITHUB_REPO` to trigger a real re-run; without them the response still says `retry`, and the orchestrator logs the error.

## 6. Terraform

```bash
cd terraform
terraform fmt -recursive
terraform init -backend=false
terraform validate
./validate-terraform.sh
terraform plan -var="environment=local" -var="enable_staging=false" -var="enable_monitoring=false"
```

Use `terraform init -backend=false` for validation without touching state. State is local (`terraform.tfstate`); never commit it, since it can contain secrets.

## 7. Documentation

The docs are MkDocs with the `shadcn` theme and live in `docs/`; navigation is in `mkdocs.yml`.

```bash
pip install mkdocs mkdocs-shadcn pymdown-extensions
mkdocs serve                 # live preview at http://127.0.0.1:8000
mkdocs build --strict        # fails on broken links and nav errors
```

Admonitions (`!!! note`), tabs (`=== "Title"`), task lists and `mermaid` fences are enabled. Draw diagrams as SVG files in `docs/assets/diagrams/` and embed them with a normal image link (for example `![Alt text](../assets/diagrams/component-map.svg)`) instead of ASCII art.

## Project Layout

```text
orchestrator/   FastAPI service (src/, tests/, k8s/, Dockerfile)
sample-app/     Flask target app (src/, tests/, k8s/, Dockerfile)
terraform/      Modules: namespaces, sample-app, orchestrator, security, monitoring
kubernetes/     Standalone security manifests
monitoring/     Prometheus, Grafana, Loki, Promtail, Alertmanager configs
scripts/        deploy.sh
.github/        Workflows
docs/           This documentation
```

## Editor and Quality Settings

- Format Python with `black` and sort imports with `isort` (`make format`). CI runs `flake8 --max-line-length=100 --extend-ignore=E203,W503` and `black --check` (non-blocking today).
- Keep type hints on new functions.
- Add comments only where the code is not self-explanatory.

## Troubleshooting

| Problem                                           | Fix                                                                           |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `ModuleNotFoundError: No module named 'src'`      | Run `pytest`/`uvicorn` from inside `orchestrator/` or `sample-app/`           |
| `RuntimeError: Redis connection failed` on import | Start Redis, or patch it in tests                                             |
| `RuntimeError: Kubernetes config failed`          | Make sure `kubectl cluster-info` works, or patch `kubernetes.config` in tests |
| `ImagePullBackOff` locally                        | `make load-images`; manifests use `imagePullPolicy: IfNotPresent`             |
| Orchestrator pod `CreateContainerConfigError`     | Create the `github-token` Secret in the `orchestrator` namespace              |
| Port already in use                               | `lsof -i :8000` and stop the process, or change the local port                |
