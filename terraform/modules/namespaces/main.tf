variable "labels" {
  description = "Common labels to apply to all namespaces"
  type        = map(string)
  default     = {}
}

resource "kubernetes_namespace" "production" {
  metadata {
    name   = "production"
    labels = merge(var.labels, { name = "production", environment = "production" })
  }
}

resource "kubernetes_namespace" "staging" {
  metadata {
    name   = "staging"
    labels = merge(var.labels, { name = "staging", environment = "staging" })
  }
}

resource "kubernetes_namespace" "canary" {
  metadata {
    name   = "canary"
    labels = merge(var.labels, { name = "canary", environment = "canary" })
  }
}

resource "kubernetes_namespace" "orchestrator" {
  metadata {
    name   = "orchestrator"
    labels = merge(var.labels, { name = "orchestrator", environment = "production" })
  }
}

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name   = "monitoring"
    labels = merge(var.labels, { name = "monitoring", environment = "production" })
  }
}
