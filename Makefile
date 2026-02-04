# Makefile for Self-Healing CI/CD Pipeline

.PHONY: help setup build test deploy clean monitoring logs

# Variables
CLUSTER_NAME ?= self-healing-cicd
NAMESPACE ?= production
APP_VERSION ?= $(shell git rev-parse --short HEAD)-$(shell date +%s)
DOCKER_REGISTRY ?= localhost:5000

# Colors
COLOR_RESET = \033[0m
COLOR_BOLD = \033[1m
COLOR_GREEN = \033[32m
COLOR_YELLOW = \033[33m
COLOR_BLUE = \033[34m

# Default target
.DEFAULT_GOAL := help

help: ## Show this help message
	@echo "$(COLOR_BOLD)Self-Healing CI/CD Pipeline - Available Commands$(COLOR_RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(COLOR_GREEN)%-20s$(COLOR_RESET) %s\n", $$1, $$2}'

## Setup Commands

setup: setup-tools setup-cluster setup-deps ## Complete setup (tools + cluster + dependencies)

setup-tools: ## Install required tools
	@echo "$(COLOR_BLUE)Installing required tools...$(COLOR_RESET)"
	@./scripts/install-tools.sh

setup-cluster: ## Setup Kubernetes cluster
	@echo "$(COLOR_BLUE)Setting up Kubernetes cluster...$(COLOR_RESET)"
	@./scripts/setup-k8s-cluster.sh

setup-deps: ## Install Python dependencies
	@echo "$(COLOR_BLUE)Installing dependencies...$(COLOR_RESET)"
	@pip install -r sample-app/requirements.txt
	@pip install -r orchestrator/requirements.txt

## Build Commands

build: build-app build-orchestrator ## Build all Docker images

build-app: ## Build sample application
	@echo "$(COLOR_BLUE)Building sample-app:$(APP_VERSION)...$(COLOR_RESET)"
	@docker build -t sample-app:$(APP_VERSION) \
		--build-arg APP_VERSION=$(APP_VERSION) \
		-f sample-app/Dockerfile sample-app/
	@docker tag sample-app:$(APP_VERSION) sample-app:latest

build-orchestrator: ## Build orchestrator
	@echo "$(COLOR_BLUE)Building orchestrator...$(COLOR_RESET)"
	@docker build -t orchestrator:latest \
		-f orchestrator/Dockerfile orchestrator/

load-images: ## Load images to cluster
	@echo "$(COLOR_BLUE)Loading images to cluster...$(COLOR_RESET)"
	@if command -v minikube > /dev/null && minikube status > /dev/null 2>&1; then \
		minikube image load sample-app:$(APP_VERSION); \
		minikube image load orchestrator:latest; \
	elif command -v kind > /dev/null; then \
		kind load docker-image sample-app:$(APP_VERSION) --name $(CLUSTER_NAME); \
		kind load docker-image orchestrator:latest --name $(CLUSTER_NAME); \
	fi

## Test Commands

test: test-app test-orchestrator ## Run all tests

test-app: ## Test sample application
	@echo "$(COLOR_BLUE)Testing sample application...$(COLOR_RESET)"
	@cd sample-app && pytest tests/ -v --cov=src --cov-report=term-missing

test-orchestrator: ## Test orchestrator
	@echo "$(COLOR_BLUE)Testing orchestrator...$(COLOR_RESET)"
	@cd orchestrator && pytest tests/ -v --cov=src --cov-report=term-missing

test-integration: ## Run integration tests
	@echo "$(COLOR_BLUE)Running integration tests...$(COLOR_RESET)"
	@./scripts/run-integration-tests.sh

lint: ## Run linters
	@echo "$(COLOR_BLUE)Running linters...$(COLOR_RESET)"
	@flake8 sample-app/src orchestrator/src
	@pylint sample-app/src orchestrator/src
	@black --check sample-app/src orchestrator/src

format: ## Format code
	@echo "$(COLOR_BLUE)Formatting code...$(COLOR_RESET)"
	@black sample-app/src orchestrator/src
	@isort sample-app/src orchestrator/src

## Deployment Commands

deploy: deploy-infra deploy-app ## Deploy everything

deploy-infra: ## Deploy infrastructure with Terraform
	@echo "$(COLOR_BLUE)Deploying infrastructure...$(COLOR_RESET)"
	@cd terraform && terraform init && terraform apply -auto-approve

deploy-app: ## Deploy application
	@echo "$(COLOR_BLUE)Deploying application...$(COLOR_RESET)"
	@./scripts/deploy.sh --namespace $(NAMESPACE) --version $(APP_VERSION)

deploy-staging: ## Deploy to staging
	@$(MAKE) deploy-app NAMESPACE=staging

deploy-production: ## Deploy to production
	@$(MAKE) deploy-app NAMESPACE=production

rollback: ## Rollback deployment
	@echo "$(COLOR_YELLOW)Rolling back deployment...$(COLOR_RESET)"
	@kubectl rollout undo deployment/sample-app -n $(NAMESPACE)

## Monitoring Commands

monitoring: ## Setup monitoring stack
	@echo "$(COLOR_BLUE)Setting up monitoring...$(COLOR_RESET)"
	@cd terraform && terraform apply -auto-approve -var="enable_monitoring=true"

port-forward-app: ## Port forward to application
	@echo "$(COLOR_GREEN)Forwarding port 8080 -> sample-app...$(COLOR_RESET)"
	@kubectl port-forward -n $(NAMESPACE) svc/sample-app-service 8080:80

port-forward-orchestrator: ## Port forward to orchestrator
	@echo "$(COLOR_GREEN)Forwarding port 8000 -> orchestrator...$(COLOR_RESET)"
	@kubectl port-forward -n orchestrator svc/orchestrator-service 8000:8000

port-forward-grafana: ## Port forward to Grafana
	@echo "$(COLOR_GREEN)Forwarding port 3000 -> Grafana...$(COLOR_RESET)"
	@echo "Username: admin, Password: admin123"
	@kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

port-forward-prometheus: ## Port forward to Prometheus
	@echo "$(COLOR_GREEN)Forwarding port 9090 -> Prometheus...$(COLOR_RESET)"
	@kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090

## Log Commands

logs-app: ## Show application logs
	@kubectl logs -n $(NAMESPACE) -l app=sample-app --tail=100 -f

logs-orchestrator: ## Show orchestrator logs
	@kubectl logs -n orchestrator -l app=orchestrator --tail=100 -f

logs-all: ## Show all logs
	@stern -n $(NAMESPACE) .

## Status Commands

status: ## Show cluster status
	@echo "$(COLOR_BOLD)Cluster Status$(COLOR_RESET)"
	@kubectl cluster-info
	@echo ""
	@echo "$(COLOR_BOLD)Namespaces$(COLOR_RESET)"
	@kubectl get namespaces
	@echo ""
	@echo "$(COLOR_BOLD)Deployments$(COLOR_RESET)"
	@kubectl get deployments --all-namespaces

pods: ## Show all pods
	@kubectl get pods --all-namespaces

services: ## Show all services
	@kubectl get services --all-namespaces

describe-app: ## Describe application deployment
	@kubectl describe deployment sample-app -n $(NAMESPACE)

events: ## Show recent events
	@kubectl get events --all-namespaces --sort-by='.lastTimestamp' | tail -20

## Utility Commands

shell-app: ## Open shell in application pod
	@kubectl exec -it -n $(NAMESPACE) $$(kubectl get pod -n $(NAMESPACE) -l app=sample-app -o jsonpath='{.items[0].metadata.name}') -- /bin/sh

shell-orchestrator: ## Open shell in orchestrator pod
	@kubectl exec -it -n orchestrator $$(kubectl get pod -n orchestrator -l app=orchestrator -o jsonpath='{.items[0].metadata.name}') -- /bin/sh

restart-app: ## Restart application
	@kubectl rollout restart deployment/sample-app -n $(NAMESPACE)

scale-app: ## Scale application (use REPLICAS=N)
	@kubectl scale deployment/sample-app -n $(NAMESPACE) --replicas=$(REPLICAS)

## Cleanup Commands

clean: ## Clean up resources
	@echo "$(COLOR_YELLOW)Cleaning up resources...$(COLOR_RESET)"
	@kubectl delete namespace production staging canary orchestrator monitoring || true

clean-all: clean ## Clean everything including cluster
	@echo "$(COLOR_YELLOW)Destroying cluster...$(COLOR_RESET)"
	@if command -v minikube > /dev/null; then minikube delete; fi
	@if command -v kind > /dev/null; then kind delete cluster --name $(CLUSTER_NAME); fi
	@cd terraform && terraform destroy -auto-approve

reset: clean-all setup build deploy ## Complete reset and redeploy

## Development Commands

dev-start: ## Start local development environment
	@echo "$(COLOR_BLUE)Starting development environment...$(COLOR_RESET)"
	@docker-compose up -d

dev-stop: ## Stop local development environment
	@docker-compose down

dev-logs: ## Show development logs
	@docker-compose logs -f

dev-test: ## Test in development environment
	@docker-compose exec orchestrator pytest tests/ -v

## Documentation Commands

docs: ## Generate documentation
	@echo "$(COLOR_BLUE)Generating documentation...$(COLOR_RESET)"
	@./scripts/generate-docs.sh

diagram: ## Generate architecture diagram
	@python scripts/generate-diagram.py

## Performance Commands

benchmark: ## Run performance benchmarks
	@echo "$(COLOR_BLUE)Running benchmarks...$(COLOR_RESET)"
	@./scripts/benchmark.sh

load-test: ## Run load tests
	@echo "$(COLOR_BLUE)Running load tests...$(COLOR_RESET)"
	@./scripts/load-test.sh

stress-test: ## Run stress tests
	@echo "$(COLOR_BLUE)Running stress tests...$(COLOR_RESET)"
	@kubectl run stress-test --image=busybox --restart=Never -- \
		wget -O- http://sample-app-service.$(NAMESPACE)/api/stress?duration=30