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