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

module "namespaces" {
  source = "./modules/namespaces"
  labels = local.common_labels
}

module "security" {
  source = "./modules/security"

  depends_on = [module.namespaces]
}

module "sample_app" {
  source = "./modules/sample-app"

  namespace      = module.namespaces.production_namespace
  app_version    = var.app_version
  replicas       = var.replicas
  image_registry = local.image_registry
  failure_rate   = var.environment == "production" ? 0.0 : var.failure_rate

  depends_on = [module.namespaces, module.security]
}

module "sample_app_staging" {
  source = "./modules/sample-app"
  count  = var.enable_staging ? 1 : 0

  namespace      = module.namespaces.staging_namespace
  app_version    = var.app_version
  replicas       = var.staging_replicas
  image_registry = local.image_registry
  failure_rate   = var.failure_rate
  ingress_host   = "sample-app.staging.local"

  depends_on = [module.namespaces, module.security]
}

module "orchestrator" {
  source = "./modules/orchestrator"

  namespace      = module.namespaces.orchestrator_namespace
  image_registry = local.image_registry

  depends_on = [module.namespaces, module.security]
}

module "monitoring" {
  source  = "./modules/monitoring"
  enabled = var.enable_monitoring

  depends_on = [module.namespaces]
}
