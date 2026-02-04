variable "enabled" {
  description = "Enable monitoring stack"
  type        = bool
  default     = true
}

# Prometheus
resource "helm_release" "prometheus" {
  count = var.enabled ? 1 : 0
  
  name       = "prometheus"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  namespace  = "monitoring"
  version    = "54.0.0"
  
  values = [
    file("${path.module}/values/prometheus-values.yaml")
  ]
  
  set {
    name  = "prometheus.prometheusSpec.retention"
    value = "7d"
  }
  
  set {
    name  = "prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage"
    value = "10Gi"
  }
  
  set {
    name  = "grafana.adminPassword"
    value = "admin123"  # Change in production!
  }
}

# Loki for log aggregation
resource "helm_release" "loki" {
  count = var.enabled ? 1 : 0
  
  name       = "loki"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki-stack"
  namespace  = "monitoring"
  version    = "2.9.11"
  
  set {
    name  = "loki.enabled"
    value = "true"
  }
  
  set {
    name  = "promtail.enabled"
    value = "true"
  }
  
  set {
    name  = "grafana.enabled"
    value = "false"  # Using Grafana from prometheus stack
  }
}

# ServiceMonitor for sample app
resource "kubernetes_manifest" "sample_app_service_monitor" {
  count = var.enabled ? 1 : 0
  
  manifest = {
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "ServiceMonitor"
    metadata = {
      name      = "sample-app-monitor"
      namespace = "production"
      labels = {
        app     = "sample-app"
        release = "prometheus"
      }
    }
    spec = {
      selector = {
        matchLabels = {
          app = "sample-app"
        }
      }
      endpoints = [
        {
          port     = "http"
          path     = "/metrics"
          interval = "30s"
        }
      ]
    }
  }
}

# ServiceMonitor for orchestrator
resource "kubernetes_manifest" "orchestrator_service_monitor" {
  count = var.enabled ? 1 : 0
  
  manifest = {
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "ServiceMonitor"
    metadata = {
      name      = "orchestrator-monitor"
      namespace = "orchestrator"
      labels = {
        app     = "orchestrator"
        release = "prometheus"
      }
    }
    spec = {
      selector = {
        matchLabels = {
          app = "orchestrator"
        }
      }
      endpoints = [
        {
          port     = "http"
          path     = "/metrics"
          interval = "15s"
        }
      ]
    }
  }
}

