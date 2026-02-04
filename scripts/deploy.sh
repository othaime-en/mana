#!/bin/bash
# Complete deployment automation script

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
NAMESPACE="${NAMESPACE:-production}"
APP_VERSION="${APP_VERSION:-1.0.0}"
DEPLOY_TIMEOUT="${DEPLOY_TIMEOUT:-600}"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    local missing_tools=()
    
    for tool in kubectl docker helm terraform; do
        if ! command -v $tool &> /dev/null; then
            missing_tools+=($tool)
        fi
    done
    
    if [ ${#missing_tools[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        log_info "Please install the missing tools and try again"
        exit 1
    fi
    
    # Check kubectl connection
    if ! kubectl cluster-info &> /dev/null; then
        log_error "Cannot connect to Kubernetes cluster"
        exit 1
    fi
    
    log_success "All prerequisites met"
}

setup_infrastructure() {
    log_info "Setting up infrastructure with Terraform..."
    
    cd "$PROJECT_ROOT/terraform"
    
    # Initialize Terraform
    terraform init
    
    # Plan
    terraform plan -out=tfplan
    
    # Apply
    terraform apply tfplan
    
    rm tfplan
    
    log_success "Infrastructure setup complete"
}

build_images() {
    log_info "Building Docker images..."
    
    # Build sample app
    log_info "Building sample-app:${APP_VERSION}..."
    docker build \
        -t sample-app:${APP_VERSION} \
        -f "$PROJECT_ROOT/sample-app/Dockerfile" \
        "$PROJECT_ROOT/sample-app"
    
    # Build orchestrator
    log_info "Building orchestrator:latest..."
    docker build \
        -t orchestrator:latest \
        -f "$PROJECT_ROOT/orchestrator/Dockerfile" \
        "$PROJECT_ROOT/orchestrator"
    
    # Load images to cluster (for Minikube/Kind)
    if command -v minikube &> /dev/null && minikube status &> /dev/null; then
        log_info "Loading images to Minikube..."
        minikube image load sample-app:${APP_VERSION}
        minikube image load orchestrator:latest
    elif command -v kind &> /dev/null; then
        log_info "Loading images to Kind..."
        kind load docker-image sample-app:${APP_VERSION}
        kind load docker-image orchestrator:latest
    fi
    
    log_success "Images built successfully"
}

deploy_redis() {
    log_info "Deploying Redis..."
    
    helm repo add bitnami https://charts.bitnami.com/bitnami
    helm repo update
    
    helm upgrade --install redis bitnami/redis \
        --namespace orchestrator \
        --set auth.enabled=false \
        --set master.persistence.size=1Gi \
        --wait
    
    log_success "Redis deployed"
}

deploy_orchestrator() {
    log_info "Deploying Self-Healing Orchestrator..."
    
    kubectl apply -f "$PROJECT_ROOT/orchestrator/k8s/" -n orchestrator
    
    # Wait for orchestrator to be ready
    kubectl wait --for=condition=available --timeout=${DEPLOY_TIMEOUT}s \
        deployment/orchestrator -n orchestrator
    
    log_success "Orchestrator deployed"
}

deploy_sample_app() {
    log_info "Deploying Sample Application to ${NAMESPACE}..."
    
    # Update image version in manifests
    sed -i.bak "s|sample-app:.*|sample-app:${APP_VERSION}|g" \
        "$PROJECT_ROOT/sample-app/k8s/deployment.yaml"
    
    # Apply manifests
    kubectl apply -f "$PROJECT_ROOT/sample-app/k8s/" -n ${NAMESPACE}
    
    # Wait for deployment
    kubectl wait --for=condition=available --timeout=${DEPLOY_TIMEOUT}s \
        deployment/sample-app -n ${NAMESPACE}
    
    # Restore original file
    mv "$PROJECT_ROOT/sample-app/k8s/deployment.yaml.bak" \
       "$PROJECT_ROOT/sample-app/k8s/deployment.yaml"
    
    log_success "Sample application deployed"
}

verify_deployment() {
    log_info "Verifying deployment..."
    
    # Check pod status
    log_info "Pod status:"
    kubectl get pods -n ${NAMESPACE} -l app=sample-app
    
    # Check service
    log_info "Service status:"
    kubectl get svc -n ${NAMESPACE} sample-app-service
    
    # Health check
    log_info "Running health check..."
    POD_NAME=$(kubectl get pods -n ${NAMESPACE} -l app=sample-app -o jsonpath='{.items[0].metadata.name}')
    
    kubectl exec -n ${NAMESPACE} $POD_NAME -- curl -f http://localhost:5000/health
    
    if [ $? -eq 0 ]; then
        log_success "Health check passed"
    else
        log_error "Health check failed"
        exit 1
    fi
    
    # Get deployment info
    log_info "Deployment information:"
    kubectl describe deployment sample-app -n ${NAMESPACE} | grep -A 10 "^Conditions:"
}

setup_monitoring() {
    log_info "Setting up monitoring stack..."
    
    # Deploy monitoring components via Terraform
    cd "$PROJECT_ROOT/terraform"
    terraform apply -auto-approve -var="enable_monitoring=true"
    
    # Wait for Grafana to be ready
    log_info "Waiting for Grafana to be ready..."
    kubectl wait --for=condition=ready pod \
        -l app.kubernetes.io/name=grafana \
        -n monitoring \
        --timeout=300s
    
    log_success "Monitoring stack deployed"
    
    # Get Grafana credentials
    log_info "Grafana credentials:"
    echo "  Username: admin"
    echo "  Password: admin123"
    echo "  URL: kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80"
}

run_smoke_tests() {
    log_info "Running smoke tests..."
    
    # Port forward to service
    kubectl port-forward -n ${NAMESPACE} svc/sample-app-service 8080:80 &
    PF_PID=$!
    
    sleep 5
    
    # Test endpoints
    local failed=0
    
    if curl -f http://localhost:8080/health > /dev/null 2>&1; then
        log_success "Health endpoint: OK"
    else
        log_error "Health endpoint: FAILED"
        failed=1
    fi
    
    if curl -f http://localhost:8080/ > /dev/null 2>&1; then
        log_success "Root endpoint: OK"
    else
        log_error "Root endpoint: FAILED"
        failed=1
    fi
    
    if curl -f http://localhost:8080/api/data > /dev/null 2>&1; then
        log_success "API endpoint: OK"
    else
        log_error "API endpoint: FAILED"
        failed=1
    fi
    
    # Cleanup
    kill $PF_PID
    
    if [ $failed -eq 1 ]; then
        log_error "Smoke tests failed"
        exit 1
    fi
    
    log_success "All smoke tests passed"
}

print_access_info() {
    log_info "=================================================="
    log_success "Deployment Complete!"
    log_info "=================================================="
    echo ""
    log_info "Access Information:"
    echo ""
    echo "Sample Application:"
    echo "  kubectl port-forward -n ${NAMESPACE} svc/sample-app-service 8080:80"
    echo "  Then visit: http://localhost:8080"
    echo ""
    echo "Orchestrator API:"
    echo "  kubectl port-forward -n orchestrator svc/orchestrator-service 8000:8000"
    echo "  Then visit: http://localhost:8000/docs"
    echo ""
    echo "Grafana Dashboard:"
    echo "  kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80"
    echo "  Then visit: http://localhost:3000"
    echo "  Username: admin, Password: admin123"
    echo ""
    echo "Prometheus:"
    echo "  kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090"
    echo "  Then visit: http://localhost:9090"
    echo ""
    log_info "=================================================="
}

# Main deployment flow
main() {
    log_info "Starting deployment process..."
    echo ""
    
    check_prerequisites
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip-infra)
                SKIP_INFRA=true
                shift
                ;;
            --skip-monitoring)
                SKIP_MONITORING=true
                shift
                ;;
            --namespace)
                NAMESPACE="$2"
                shift 2
                ;;
            --version)
                APP_VERSION="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    # Execute deployment steps
    if [ -z "$SKIP_INFRA" ]; then
        setup_infrastructure
    fi
    
    build_images
    deploy_redis
    deploy_orchestrator
    deploy_sample_app
    verify_deployment
    
    if [ -z "$SKIP_MONITORING" ]; then
        setup_monitoring
    fi
    
    run_smoke_tests
    print_access_info
    
    log_success "Deployment completed successfully!"
}

# Run main function
main "$@"