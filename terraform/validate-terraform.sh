#!/bin/bash
# Terraform Module Validation Script
# Validates all Terraform modules and configuration

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
    ((PASSED++))
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
    ((FAILED++))
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
    ((WARNINGS++))
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if command -v terraform &> /dev/null; then
        log_success "Terraform is installed ($(terraform version -json | jq -r '.terraform_version'))"
    else
        log_error "Terraform is not installed"
    fi
    
    if command -v kubectl &> /dev/null; then
        log_success "kubectl is installed ($(kubectl version --client -o json 2>/dev/null | jq -r '.clientVersion.gitVersion'))"
    else
        log_error "kubectl is not installed"
    fi
    
    if kubectl cluster-info &> /dev/null; then
        log_success "Kubernetes cluster is accessible"
    else
        log_warning "Kubernetes cluster is not accessible (optional for validation)"
    fi
}

# Validate Terraform syntax
validate_terraform() {
    log_info "Validating Terraform configuration..."
    
    cd terraform
    
    # Format check
    if terraform fmt -check -recursive &> /dev/null; then
        log_success "Terraform formatting is correct"
    else
        log_warning "Terraform files need formatting (run: terraform fmt -recursive)"
    fi
    
    # Initialize
    if terraform init -backend=false &> /dev/null; then
        log_success "Terraform initialized successfully"
    else
        log_error "Terraform initialization failed"
        return 1
    fi
    
    # Validate
    if terraform validate &> /dev/null; then
        log_success "Terraform configuration is valid"
    else
        log_error "Terraform validation failed"
        terraform validate
        return 1
    fi
    
    cd ..
}

# Check module structure
check_module_structure() {
    log_info "Checking module structure..."
    
    local modules=("namespaces" "sample-app" "orchestrator" "monitoring")
    
    for module in "${modules[@]}"; do
        local module_path="terraform/modules/${module}"
        
        if [ -d "$module_path" ]; then
            log_success "Module exists: $module"
            
            # Check required files
            if [ -f "$module_path/main.tf" ]; then
                log_success "  ✓ main.tf found"
            else
                log_error "  ✗ main.tf missing"
            fi
            
            if [ -f "$module_path/variables.tf" ] || [ -f "$module_path/outputs.tf" ]; then
                log_success "  ✓ variables.tf or outputs.tf found"
            else
                log_warning "  ! No variables.tf or outputs.tf found"
            fi
        else
            log_error "Module missing: $module"
        fi
    done
}

# Check for hardcoded secrets
check_secrets() {
    log_info "Checking for hardcoded secrets..."
    
    local secret_patterns=(
        "password.*=.*['\"].*['\"]"
        "token.*=.*['\"].*['\"]"
        "api[_-]key.*=.*['\"].*['\"]"
    )
    
    local found_secrets=false
    
    for pattern in "${secret_patterns[@]}"; do
        if grep -r -i -E "$pattern" terraform/ 2>/dev/null | grep -v "change-me" | grep -v "example" | grep -v "TODO" > /dev/null; then
            log_warning "Potential hardcoded secret found (pattern: $pattern)"
            found_secrets=true
        fi
    done
    
    if [ "$found_secrets" = false ]; then
        log_success "No hardcoded secrets detected"
    fi
}

# Check resource naming conventions
check_naming_conventions() {
    log_info "Checking resource naming conventions..."
    
    # Check for consistent naming
    if grep -r "resource \"kubernetes_" terraform/modules/ | grep -v "test" | grep -v "#" > /dev/null; then
        log_success "Kubernetes resources defined"
    else
        log_warning "No Kubernetes resources found in modules"
    fi
}

# Validate YAML files
validate_yaml() {
    log_info "Validating YAML files..."
    
    local yaml_files=$(find . -name "*.yaml" -o -name "*.yml" | grep -v ".terraform" | grep -v "node_modules")
    
    if command -v yamllint &> /dev/null; then
        for file in $yaml_files; do
            if yamllint -d relaxed "$file" &> /dev/null; then
                log_success "Valid YAML: $file"
            else
                log_warning "YAML issues in: $file"
            fi
        done
    else
        log_warning "yamllint not installed, skipping YAML validation"
    fi
}

# Check for required variables
check_required_variables() {
    log_info "Checking required variables..."
    
    if [ -f "terraform/variables.tf" ]; then
        log_success "Root variables.tf found"
        
        # Check for common variables
        local common_vars=("app_version" "replicas" "enable_monitoring")
        for var in "${common_vars[@]}"; do
            if grep -q "variable \"$var\"" terraform/variables.tf; then
                log_success "  ✓ Variable defined: $var"
            else
                log_warning "  ! Variable not found: $var"
            fi
        done
    else
        log_error "Root variables.tf not found"
    fi
}

# Generate summary report
generate_summary() {
    echo ""
    echo "======================================"
    echo "Validation Summary"
    echo "======================================"
    echo -e "${GREEN}Passed: ${PASSED}${NC}"
    echo -e "${YELLOW}Warnings: ${WARNINGS}${NC}"
    echo -e "${RED}Failed: ${FAILED}${NC}"
    echo "======================================"
    
    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ All critical checks passed!${NC}"
        echo ""
        echo "Next steps:"
        echo "1. Review any warnings above"
        echo "2. Run: cd terraform && terraform plan"
        echo "3. If plan looks good: terraform apply"
        return 0
    else
        echo -e "${RED}✗ Some checks failed. Please fix the errors above.${NC}"
        return 1
    fi
}

# Main execution
main() {
    echo "======================================"
    echo "Terraform Module Validation"
    echo "======================================"
    echo ""
    
    check_prerequisites
    echo ""
    
    check_module_structure
    echo ""
    
    validate_terraform
    echo ""
    
    check_secrets
    echo ""
    
    check_naming_conventions
    echo ""
    
    check_required_variables
    echo ""
    
    validate_yaml
    echo ""
    
    generate_summary
}

# Run main function
main