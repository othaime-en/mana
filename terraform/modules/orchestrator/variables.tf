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

variable "redis_password" {
  description = "Redis password (leave empty for no auth)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "max_retries" {
  description = "Maximum deployment retry attempts"
  type        = number
  default     = 3
}

variable "rollback_threshold" {
  description = "Number of failures before rollback"
  type        = number
  default     = 2
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
