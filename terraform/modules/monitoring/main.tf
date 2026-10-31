# terraform/modules/monitoring/main.tf
variable "enabled" {
  description = "Enable monitoring stack"
  type        = bool
  default     = true
}

resource "random_password" "grafana_admin" {
  length  = 32
  special = false
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
  
  set_sensitive {
    name  = "grafana.adminPassword"
    value = random_password.grafana_admin.result
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
    value = "false" # Promtail is end-of-life; Alloy ships the logs
  }
  
  set {
    name  = "grafana.enabled"
    value = "false"  # Using Grafana from prometheus stack
  }
}

# Alloy ships pod logs and cluster events to Loki. loki.source.kubernetes reads
# logs through the API server, so a single replica is right: a DaemonSet would
# send every pod's logs once per node.
resource "helm_release" "alloy" {
  count = var.enabled ? 1 : 0

  name       = "alloy"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "alloy"
  namespace  = "monitoring"
  version    = "1.13.1"

  values = [yamlencode({
    image = {
      tag = "v1.21.2"
    }
    controller = {
      type     = "deployment"
      replicas = 1
    }
    alloy = {
      configMap = {
        content = file("${local.monitoring_dir}/alloy/config.alloy")
      }
      extraEnv = [{
        name  = "LOKI_URL"
        value = "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push"
      }]
    }
    rbac = {
      rules = [{
        apiGroups = [""]
        resources = ["pods", "pods/log", "namespaces", "events"]
        verbs     = ["get", "list", "watch"]
      }]
    }
  })]

  depends_on = [helm_release.loki]
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

# Rules and dashboards come from monitoring/, the same files Compose mounts
locals {
  monitoring_dir = "${path.module}/../../../monitoring"
  rule_files     = ["alerts.yml", "recording-rules.yml"]
  rule_groups = flatten([
    for file in local.rule_files :
    yamldecode(file("${local.monitoring_dir}/prometheus/${file}")).groups
  ])
  dashboard_files = fileset("${local.monitoring_dir}/grafana/dashboards", "*.json")
}

resource "kubernetes_manifest" "mana_rules" {
  count = var.enabled ? 1 : 0

  manifest = {
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "PrometheusRule"
    metadata = {
      name      = "mana-rules"
      namespace = "monitoring"
      labels = {
        release = "prometheus"
      }
    }
    spec = {
      groups = local.rule_groups
    }
  }
}

resource "kubernetes_config_map" "grafana_dashboards" {
  for_each = var.enabled ? local.dashboard_files : toset([])

  metadata {
    name      = "mana-dashboard-${trimsuffix(each.value, ".json")}"
    namespace = "monitoring"
    labels = {
      grafana_dashboard = "1"
    }
  }

  data = {
    (each.value) = file("${local.monitoring_dir}/grafana/dashboards/${each.value}")
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