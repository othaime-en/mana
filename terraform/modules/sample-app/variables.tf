variable "namespace" {
  description = "Kubernetes namespace for sample app"
  type        = string
  default     = "production"
}

variable "app_version" {
  description = "Application version/tag"
  type        = string
  default     = "1.0.0"
}

variable "replicas" {
  description = "Number of pod replicas"
  type        = number
  default     = 3

  validation {
    condition     = var.replicas >= 1 && var.replicas <= 20
    error_message = "Replicas must be between 1 and 20."
  }
}

variable "image_registry" {
  description = "Container image registry"
  type        = string
  default     = ""
}

variable "image_pull_policy" {
  description = "Image pull policy"
  type        = string
  default     = "IfNotPresent"
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
      cpu    = "100m"
      memory = "128Mi"
    }
    limits = {
      cpu    = "200m"
      memory = "256Mi"
    }
  }
}

variable "enable_hpa" {
  description = "Enable Horizontal Pod Autoscaler"
  type        = bool
  default     = true
}

variable "hpa_min_replicas" {
  description = "Minimum replicas for HPA"
  type        = number
  default     = 3
}

variable "hpa_max_replicas" {
  description = "Maximum replicas for HPA"
  type        = number
  default     = 10
}

variable "ingress_host" {
  description = "Ingress host"
  type        = string
  default     = "sample-app.local"
}

variable "failure_rate" {
  description = "Failure simulation rate injected as FAILURE_RATE env var (0.0 = disabled, production should always be 0.0)"
  type        = number
  default     = 0.0

  validation {
    condition     = var.failure_rate >= 0.0 && var.failure_rate <= 1.0
    error_message = "Failure rate must be between 0.0 and 1.0."
  }
}
