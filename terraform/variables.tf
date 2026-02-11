variable "environment" {
  description = "Environment name (local, dev, staging, production)"
  type        = string
  default     = "production"

  validation {
    condition     = contains(["local", "dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: local, dev, staging, production."
  }
}

variable "app_version" {
  description = "Application version/image tag to deploy"
  type        = string
  default     = "1.0.0"
}

variable "replicas" {
  description = "Number of replicas for the production sample-app deployment"
  type        = number
  default     = 3

  validation {
    condition     = var.replicas >= 1 && var.replicas <= 20
    error_message = "Replicas must be between 1 and 20."
  }
}

variable "staging_replicas" {
  description = "Number of replicas for the staging sample-app deployment"
  type        = number
  default     = 2
}

variable "enable_monitoring" {
  description = "Deploy the Prometheus/Grafana/Loki monitoring stack"
  type        = bool
  default     = true
}

variable "enable_staging" {
  description = "Deploy sample-app to the staging namespace"
  type        = bool
  default     = true
}

variable "image_registry" {
  description = "Container image registry prefix (e.g. ghcr.io/org). Leave empty for local cluster images."
  type        = string
  default     = ""
}

variable "failure_rate" {
  description = "Failure simulation rate for the sample-app (0.0 = disabled, 1.0 = always fail). Only meaningful in non-production environments."
  type        = number
  default     = 0.0

  validation {
    condition     = var.failure_rate >= 0.0 && var.failure_rate <= 1.0
    error_message = "Failure rate must be between 0.0 and 1.0."
  }
}
