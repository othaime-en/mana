# terraform/modules/monitoring/main.tf
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

# PrometheusRule for alerts
resource "kubernetes_manifest" "deployment_alerts" {
  count = var.enabled ? 1 : 0
  
  manifest = {
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "PrometheusRule"
    metadata = {
      name      = "deployment-alerts"
      namespace = "monitoring"
      labels = {
        release = "prometheus"
      }
    }
    spec = {
      groups = [
        {
          name = "deployment"
          rules = [
            {
              alert = "DeploymentReplicasMismatch"
              expr  = "kube_deployment_spec_replicas != kube_deployment_status_replicas_available"
              for   = "5m"
              labels = {
                severity = "warning"
              }
              annotations = {
                summary     = "Deployment {{ $labels.namespace }}/{{ $labels.deployment }} has mismatched replicas"
                description = "Deployment {{ $labels.deployment }} has {{ $value }} replicas available but {{ $labels.spec_replicas }} specified"
              }
            },
            {
              alert = "HighRollbackRate"
              expr  = "rate(rollbacks_total[5m]) > 0.1"
              for   = "2m"
              labels = {
                severity = "critical"
              }
              annotations = {
                summary     = "High rollback rate detected"
                description = "Rollback rate is {{ $value }} per second in namespace {{ $labels.namespace }}"
              }
            },
            {
              alert = "DeploymentFailureRate"
              expr  = "rate(deployments_total{status=\"failed\"}[10m]) > 0.2"
              for   = "5m"
              labels = {
                severity = "warning"
              }
              annotations = {
                summary     = "High deployment failure rate"
                description = "Deployment failure rate is {{ $value }} per second"
              }
            }
          ]
        }
      ]
    }
  }
}

# Grafana dashboard ConfigMap
resource "kubernetes_config_map" "grafana_dashboard" {
  count = var.enabled ? 1 : 0
  
  metadata {
    name      = "self-healing-dashboard"
    namespace = "monitoring"
    labels = {
      grafana_dashboard = "1"
    }
  }
  
  data = {
    "self-healing-dashboard.json" = file("${path.module}/dashboards/self-healing.json")
  }
}

# Outputs
output "grafana_url" {
  description = "Grafana URL"
  value       = var.enabled ? "http://localhost:3000" : null
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = var.enabled ? "http://localhost:9090" : null
}