# Deploys the sample application to Kubernetes

locals {
  app_name = "sample-app"
  image    = var.image_registry != "" ? "${var.image_registry}/${local.app_name}:${var.app_version}" : "${local.app_name}:${var.app_version}"
  labels = {
    app     = local.app_name
    version = var.app_version
  }
}

# Deployment
resource "kubernetes_deployment" "sample_app" {
  metadata {
    name      = local.app_name
    namespace = var.namespace
    labels    = local.labels
    annotations = {
      "deployment.kubernetes.io/revision" = "1"
    }
  }

  spec {
    replicas = var.replicas

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = "1"
        max_unavailable = "1"
      }
    }

    selector {
      match_labels = {
        app = local.app_name
      }
    }

    template {
      metadata {
        labels = local.labels
        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "5000"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        container {
          name              = local.app_name
          image             = local.image
          image_pull_policy = var.image_pull_policy

          port {
            container_port = 5000
            name           = "http"
            protocol       = "TCP"
          }

          env {
            name  = "APP_VERSION"
            value = var.app_version
          }

          env {
            name  = "ENVIRONMENT"
            value = var.namespace
          }

          env {
            name  = "PORT"
            value = "5000"
          }

          resources {
            requests = {
              cpu    = var.resources.requests.cpu
              memory = var.resources.requests.memory
            }
            limits = {
              cpu    = var.resources.limits.cpu
              memory = var.resources.limits.memory
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 5000
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            success_threshold     = 1
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 5000
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            timeout_seconds       = 3
            success_threshold     = 1
            failure_threshold     = 3
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }
        }
      }
    }
  }
}

# Service
resource "kubernetes_service" "sample_app" {
  metadata {
    name      = "${local.app_name}-service"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    type             = "ClusterIP"
    session_affinity = "ClientIP"

    selector = {
      app = local.app_name
    }

    port {
      port        = 80
      target_port = 5000
      protocol    = "TCP"
      name        = "http"
    }
  }
}

# Ingress
resource "kubernetes_ingress_v1" "sample_app" {
  metadata {
    name      = "${local.app_name}-ingress"
    namespace = var.namespace
    annotations = {
      "nginx.ingress.kubernetes.io/rewrite-target" = "/"
      "nginx.ingress.kubernetes.io/ssl-redirect"   = "false"
    }
  }

  spec {
    ingress_class_name = "nginx"

    rule {
      host = var.ingress_host

      http {
        path {
          path      = "/"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.sample_app.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }
      }
    }
  }
}

# Pod Disruption Budget
resource "kubernetes_pod_disruption_budget_v1" "sample_app" {
  metadata {
    name      = "${local.app_name}-pdb"
    namespace = var.namespace
  }

  spec {
    min_available = 2

    selector {
      match_labels = {
        app = local.app_name
      }
    }
  }
}

# Horizontal Pod Autoscaler
resource "kubernetes_horizontal_pod_autoscaler_v2" "sample_app" {
  count = var.enable_hpa ? 1 : 0

  metadata {
    name      = "${local.app_name}-hpa"
    namespace = var.namespace
  }

  spec {
    min_replicas = var.hpa_min_replicas
    max_replicas = var.hpa_max_replicas

    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.sample_app.metadata[0].name
    }

    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 70
        }
      }
    }

    metric {
      type = "Resource"
      resource {
        name = "memory"
        target {
          type                = "Utilization"
          average_utilization = 80
        }
      }
    }

    behavior {
      scale_down {
        stabilization_window_seconds = 300
        policy {
          type           = "Percent"
          value          = 50
          period_seconds = 60
        }
      }

      scale_up {
        stabilization_window_seconds = 0
        select_policy                = "Max"
        policy {
          type           = "Percent"
          value          = 100
          period_seconds = 30
        }
        policy {
          type           = "Pods"
          value          = 2
          period_seconds = 30
        }
      }
    }
  }
}
