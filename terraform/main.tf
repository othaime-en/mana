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
}

provider "kubernetes" {
  config_path = "~/.kube/config"
}

provider "helm" {
  kubernetes {
    config_path = "~/.kube/config"
  }
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
