variable "namespace" {
  description = "Kubernetes namespace for orchestrator"
  type        = string
  default     = "orchestrator"
}

variable "orchestrator_version" {
  description = "Orchestrator version/tag"
  type        = string
  default     = "latest"
}

variable "replicas" {
  description = "Number of orchestrator replicas"
  type        = number
  default     = 2
}

variable "image_registry" {
  description = "Container image registry"
  type        = string
  default     = ""
}

variable "redis_enabled" {
  description = "Deploy Redis as part of this module"
  type        = bool
  default     = true
}

variable "max_retries" {
  description = "Maximum deployment retry attempts"
  type        = number
  default     = 3
}

variable "prometheus_url" {
  description = "Prometheus the orchestrator queries for post-deploy verification (used only when the policy enables verification)"
  type        = string
  default     = "http://prometheus-kube-prometheus-prometheus.monitoring.svc:9090"
}

variable "watcher_enabled" {
  description = "Detect stuck rollouts of Deployments labelled mana.io/managed=true from the cluster"
  type        = bool
  default     = true
}

variable "watcher_namespaces" {
  description = "Namespaces the watcher scans; each needs the orchestrator Role (see managed_namespaces)"
  type        = list(string)
  default     = ["production", "staging"]
}

variable "resources" {
  description = "Resource requests and limits"
  type = object({
    requests = object({
      cpu    = string
      memory = string
    })
    limits = object({
      cpu    = string
      memory = string
    })
  })
  default = {
    requests = {
      cpu    = "200m"
      memory = "256Mi"
    }
    limits = {
      cpu    = "500m"
      memory = "512Mi"
    }
  }
}

variable "managed_namespaces" {
  description = "Namespaces whose Deployments the orchestrator may read and patch"
  type        = list(string)
  default     = ["production", "staging", "canary"]
}

variable "redis_storage_size" {
  description = "Size of the persistent volume that holds Redis data (deployment state, job queue, last good versions)"
  type        = string
  default     = "1Gi"
}
