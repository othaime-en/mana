# CLI Reference

MANA does not ship a standalone command-line client. Day-to-day operations use four tools that the repository already provides or assumes: **`make`** (shortcuts), **`scripts/deploy.sh`** (end-to-end deployment), **`docker compose`** (local stack) and **`curl`/`kubectl`** (talking to the running system). This page documents each.

!!! warning "Some Makefile targets point at scripts that do not exist yet"
The Makefile references seven helper scripts, but only `scripts/deploy.sh` is present in the repository. Targets that need a missing script fail with `No such file or directory`. They are marked **missing script** below. Either add the script or run the equivalent manual steps shown.

Run `make` or `make help` at any time to list targets with descriptions.

## Makefile

Variables can be overridden per invocation, for example `make deploy-app NAMESPACE=staging APP_VERSION=1.2.3`.

| Variable          | Default                            |
| ----------------- | ---------------------------------- |
| `CLUSTER_NAME`    | `self-healing-cicd`                |
| `NAMESPACE`       | `production`                       |
| `APP_VERSION`     | `<git short sha>-<unix timestamp>` |
| `DOCKER_REGISTRY` | `localhost:5000` (unused)          |

### Setup

| Target               | What it does                                                      | Status                    |
| -------------------- | ----------------------------------------------------------------- | ------------------------- |
| `make setup`         | `setup-tools` + `setup-cluster` + `setup-deps`                    | partly **missing script** |
| `make setup-tools`   | Runs `scripts/install-tools.sh`                                   | **missing script**        |
| `make setup-cluster` | Runs `scripts/setup-k8s-cluster.sh`                               | **missing script**        |
| `make setup-deps`    | `pip install -r` for `sample-app` and `orchestrator` requirements | works                     |

Manual equivalent of `setup-cluster`:

```bash
kind create cluster --name self-healing-cicd      # or: minikube start
kubectl create namespace production        # namespaces are also created by Terraform
kubectl create namespace orchestrator
```

### Build

| Target                    | What it does                                                                    |
| ------------------------- | ------------------------------------------------------------------------------- |
| `make build`              | `build-app` + `build-orchestrator`                                              |
| `make build-app`          | `docker build -t sample-app:$(APP_VERSION)` and tag `latest`                    |
| `make build-orchestrator` | `docker build -t orchestrator:latest`                                           |
| `make load-images`        | Loads both images into Minikube (if running) or Kind (`--name $(CLUSTER_NAME)`) |

### Test and quality

| Target                   | What it does                                                | Status                   |
| ------------------------ | ----------------------------------------------------------- | ------------------------ |
| `make test`              | `test-app` + `test-orchestrator`                            | works                    |
| `make test-app`          | `pytest tests/ -v --cov=src` in `sample-app/`               | works                    |
| `make test-orchestrator` | Same, in `orchestrator/`                                    | works                    |
| `make test-integration`  | Runs `scripts/run-integration-tests.sh`                     | **missing script**       |
| `make lint`              | `flake8`, `pylint` and `black --check` on both `src/` trees | works if tools installed |
| `make format`            | `black` and `isort` on both `src/` trees                    | works if tools installed |

### Deploy

| Target                                           | What it does                                                                                                               |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `make deploy`                                    | `deploy-infra` then `deploy-app`                                                                                           |
| `make deploy-infra`                              | `terraform init && terraform apply -auto-approve` in `terraform/`                                                          |
| `make deploy-app`                                | `./scripts/deploy.sh --namespace $(NAMESPACE) --version $(APP_VERSION)`                                                    |
| `make deploy-staging` / `make deploy-production` | `deploy-app` with the namespace preset                                                                                     |
| `make rollback`                                  | `kubectl rollout undo deployment/sample-app -n $(NAMESPACE)` (a manual Kubernetes rollback that bypasses the orchestrator) |
| `make monitoring`                                | `terraform apply -auto-approve -var="enable_monitoring=true"`                                                              |

!!! note
`scripts/deploy.sh` also runs Terraform unless you pass `--skip-infra`, so `make deploy` applies Terraform twice. Use `make deploy-app` alone (it still runs Terraform once) or call the script with `--skip-infra`.

### Access and logs

| Target                           | What it does                                                                 |
| -------------------------------- | ---------------------------------------------------------------------------- |
| `make port-forward-app`          | `localhost:8080` to sample app service                                       |
| `make port-forward-orchestrator` | `localhost:8000` to orchestrator                                             |
| `make port-forward-grafana`      | `localhost:3000` to Grafana (prints the default credentials)                 |
| `make port-forward-prometheus`   | `localhost:9090` to Prometheus                                               |
| `make logs-app`                  | Follow sample app logs (last 100 lines)                                      |
| `make logs-orchestrator`         | Follow orchestrator logs                                                     |
| `make logs-all`                  | `stern -n $(NAMESPACE) .` (requires [stern](https://github.com/stern/stern)) |

### Status and utilities

| Target                                      | What it does                                                   |
| ------------------------------------------- | -------------------------------------------------------------- |
| `make status`                               | Cluster info, namespaces and deployments across all namespaces |
| `make pods`, `make services`, `make events` | Cluster-wide pods, services and the last 20 events             |
| `make describe-app`                         | `kubectl describe deployment sample-app`                       |
| `make shell-app`, `make shell-orchestrator` | Open `/bin/sh` in the first matching pod                       |
| `make restart-app`                          | `kubectl rollout restart deployment/sample-app`                |
| `make scale-app REPLICAS=N`                 | Scale the sample app                                           |

### Cleanup

| Target           | What it does                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------ |
| `make clean`     | Deletes the `production`, `staging`, `canary`, `orchestrator` and `monitoring` namespaces  |
| `make clean-all` | `clean`, then deletes the Minikube/Kind cluster and runs `terraform destroy -auto-approve` |
| `make reset`     | `clean-all setup build deploy`                                                             |

!!! danger
`clean`, `clean-all` and `reset` delete namespaces and run `terraform destroy -auto-approve` with no confirmation. Never run them against a cluster you care about.

### Local stack and extras

| Target                                     | What it does                                                    | Status                                                                                       |
| ------------------------------------------ | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `make dev-start` / `dev-stop` / `dev-logs` | `docker-compose up -d` / `down` / `logs -f`                     | works (see [Development Setup](../contributing/development-setup.md#4-docker-compose-stack)) |
| `make dev-test`                            | `docker-compose exec orchestrator pytest tests/ -v`             | the image copies only `src/`, so there are no tests to run inside it                         |
| `make docs`                                | Runs `scripts/generate-docs.sh`                                 | **missing script**                                                                           |
| `make diagram`                             | Runs `scripts/generate-diagram.py`                              | **missing script**                                                                           |
| `make benchmark`                           | Runs `scripts/benchmark.sh`                                     | **missing script**                                                                           |
| `make load-test`                           | Runs `scripts/load-test.sh`                                     | **missing script**                                                                           |
| `make stress-test`                         | Runs a one-off busybox pod that calls `/api/stress?duration=30` | works                                                                                        |

To build and preview this documentation site instead, use MkDocs directly:

```bash
pip install mkdocs mkdocs-shadcn pymdown-extensions
mkdocs serve        # http://127.0.0.1:8000
mkdocs build --strict
```

## `scripts/deploy.sh`

Orchestrates a full deployment: prerequisite check, Terraform, image build and load, Redis, orchestrator, sample app, verification, monitoring and smoke tests.

```text
./scripts/deploy.sh [--namespace NAME] [--version TAG] [--skip-infra] [--skip-monitoring]
```

| Option              | Default                        | Description                                |
| ------------------- | ------------------------------ | ------------------------------------------ |
| `--namespace NAME`  | `production` (or `$NAMESPACE`) | Namespace for the sample app               |
| `--version TAG`     | `1.0.0` (or `$APP_VERSION`)    | Image tag to build and deploy              |
| `--skip-infra`      | off                            | Skip `terraform init/plan/apply`           |
| `--skip-monitoring` | off                            | Skip the monitoring stack and Grafana wait |

Environment: `NAMESPACE`, `APP_VERSION`, `DEPLOY_TIMEOUT` (seconds per `kubectl wait`, default `600`).

Steps, in order:

1. Verify `kubectl`, `docker`, `helm` and `terraform` are installed and the cluster is reachable.
2. `terraform init`, `plan -out=tfplan`, `apply` (unless `--skip-infra`).
3. Build `sample-app:<version>` and `orchestrator:latest`; load them into Minikube or Kind if detected.
4. Install a Bitnami Redis with Helm into the `orchestrator` namespace.
5. `kubectl apply -f orchestrator/k8s/` and wait for the orchestrator Deployment.
6. Temporarily rewrite the image tag in `sample-app/k8s/deployment.yaml`, apply it, wait, and restore the file.
7. Verify pods, service and `/health` from inside a pod.
8. `terraform apply -var="enable_monitoring=true"` and wait for Grafana (unless `--skip-monitoring`).
9. Port-forward and smoke-test `/health`, `/` and `/api/data`; print access information.

Exit code is `0` on success and `1` on any error (`set -e` plus explicit checks).

!!! note "Things to know" - `orchestrator/k8s/deployment.yaml` expects a Secret named `github-token` and a `GITHUB_REPO` you have edited; create the Secret before running the script or the orchestrator pod will not start. - Step 4 installs Redis through Helm (service `redis-master`), while `orchestrator/k8s/redis.yaml` also deploys `redis-service`, which is the host the orchestrator is configured to use. - Terraform and the manifests in step 5/6 can both manage the same Deployments. Pick one source of truth to avoid drift.

## `terraform/validate-terraform.sh`

Checks tool availability (Terraform, kubectl, optional cluster access) and validates the Terraform root module and sub-modules, printing a pass/fail/warning summary.

```bash
cd terraform && ./validate-terraform.sh
```

Typical manual Terraform workflow:

```bash
cd terraform
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -var="environment=local" -var="enable_staging=false"
terraform apply
terraform destroy
```

## Docker Compose

```bash
docker compose up -d                    # redis, sample-app, orchestrator, prometheus, grafana, loki, alloy
docker compose ps
docker compose logs -f orchestrator
docker compose up -d --build sample-app # rebuild one service
docker compose down -v                  # stop and delete volumes
```

## Operating a Running System

Replace `$MANA` with your orchestrator URL (see the [API Reference](api.md)).

### Orchestrator

```bash
# Is it healthy, and are Redis/Kubernetes reachable?
curl -s $MANA/health | jq

# Which settings are active?
curl -s $MANA/config | jq

# What happened to a deployment?
curl -s $MANA/deployment/<deployment-id> | jq
curl -s "$MANA/deployments/recent?namespace=production&limit=20" | jq

# Manual rollback through MANA (audited, verified)
curl -s -X POST $MANA/rollback -H 'Content-Type: application/json' \
  -d '{"namespace":"production","deployment_name":"sample-app","target_version":"<tag>"}'

# Simulate a failure event
curl -s -X POST $MANA/webhook/deployment -H 'Content-Type: application/json' \
  -d '{"deployment_id":"demo-1","namespace":"production","app_name":"sample-app","version":"v2","status":"failed","failure_type":"health_check_failure","metadata":{"workflow_run_id":"1"}}'
```

### Logs and audit trail

```bash
kubectl logs -n orchestrator -l app=orchestrator --tail=200 -f
kubectl logs -n orchestrator -l app=orchestrator | grep "AUDIT:"
kubectl logs -n orchestrator -l app=orchestrator | grep "<deployment-id>"
```

### Redis inspection

```bash
kubectl exec -n orchestrator deploy/redis -- redis-cli keys 'deployment:*'
kubectl exec -n orchestrator deploy/redis -- redis-cli get 'last_successful:production:sample-app'
kubectl exec -n orchestrator deploy/redis -- redis-cli ttl 'deployment:<deployment-id>'
```

### Sample app

```bash
kubectl get pods -n production -l app=sample-app -w
kubectl rollout history deployment/sample-app -n production
kubectl set env deployment/sample-app FAILURE_RATE=0.5 -n staging
kubectl set image deployment/sample-app sample-app=ghcr.io/<owner>/mana/sample-app:<tag> -n staging
```

### Monitoring

```bash
make port-forward-prometheus   # then open http://localhost:9090/targets
make port-forward-grafana      # http://localhost:3000 (admin; password from the prometheus-grafana secret)
curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=sum(rate(deployments_total[5m])) by (status)' | jq
```

## Exit Codes and Output Conventions

| Tool                                         | Success | Failure                                       |
| -------------------------------------------- | ------- | --------------------------------------------- |
| `scripts/deploy.sh`, `validate-terraform.sh` | `0`     | `1` (and a colour-coded `[ERROR]`/`[✗]` line) |
| `make`                                       | `0`     | `2` (Make's code for a failed recipe)         |
| `curl --fail`                                | `0`     | `22` for HTTP 4xx/5xx                         |

Scripts print `[INFO]`, `[SUCCESS]`, `[WARNING]` and `[ERROR]` prefixes with ANSI colours; pipe through `sed 's/\x1b\[[0-9;]*m//g'` if you need plain text in CI logs.
