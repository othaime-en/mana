terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
  }
  
  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "kubernetes" {
  config_path = "~/.kube/config"
}

provider "helm" {
  kubernetes {
    config_path = "~/.kube/config"
  }
}

# Variables
variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "app_version" {
  description = "Application version"
  type        = string
  default     = "1.0.0"
}

variable "replicas" {
  description = "Number of replicas"
  type        = number
  default     = 3
}

variable "enable_monitoring" {
  description = "Enable monitoring stack"
  type        = bool
  default     = true
}

# Modules
module "namespaces" {
  source = "./modules/namespaces"
}

module "sample_app" {
  source      = "./modules/sample-app"
  namespace   = "production"
  app_version = var.app_version
  replicas    = var.replicas
  
  depends_on = [module.namespaces]
}

module "orchestrator" {
  source    = "./modules/orchestrator"
  namespace = "orchestrator"
  
  depends_on = [module.namespaces]
}

module "monitoring" {
  source  = "./modules/monitoring"
  enabled = var.enable_monitoring
  
  depends_on = [module.namespaces]
}

# Outputs
output "sample_app_service_url" {
  description = "Sample app service URL"
  value       = module.sample_app.service_url
}

output "orchestrator_url" {
  description = "Orchestrator API URL"
  value       = module.orchestrator.api_url
}

output "grafana_url" {
  description = "Grafana dashboard URL"
  value       = var.enable_monitoring ? module.monitoring.grafana_url : null
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = var.enable_monitoring ? module.monitoring.prometheus_url : null
}