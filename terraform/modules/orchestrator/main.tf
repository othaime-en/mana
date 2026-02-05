# Deploys the self-healing orchestrator and Redis

locals {
  app_name = "orchestrator"
  image    = var.image_registry != "" ? "${var.image_registry}/${local.app_name}:${var.orchestrator_version}" : "${local.app_name}:${var.orchestrator_version}"
  labels = {
    app     = local.app_name
    version = var.orchestrator_version
  }
}

# Service Account for Orchestrator
resource "kubernetes_service_account" "orchestrator" {
  metadata {
    name      = "${local.app_name}-sa"
    namespace = var.namespace
  }
}

# ClusterRole for Orchestrator
resource "kubernetes_cluster_role" "orchestrator" {
  metadata {
    name = "${local.app_name}-role"
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "replicasets"]
    verbs      = ["get", "list", "watch", "update", "patch"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "services"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = [""]
    resources  = ["events"]
    verbs      = ["create", "patch"]
  }
}

# ClusterRoleBinding
resource "kubernetes_cluster_role_binding" "orchestrator" {
  metadata {
    name = "${local.app_name}-binding"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.orchestrator.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.orchestrator.metadata[0].name
    namespace = var.namespace
  }
}

# ConfigMap for Orchestrator Configuration
resource "kubernetes_config_map" "orchestrator" {
  metadata {
    name      = "${local.app_name}-config"
    namespace = var.namespace
  }

  data = {
    MAX_RETRIES        = tostring(var.max_retries)
    ROLLBACK_THRESHOLD = tostring(var.rollback_threshold)
    REDIS_HOST         = var.redis_enabled ? "redis-service" : "redis"
    REDIS_PORT         = "6379"
    LOG_LEVEL          = "INFO"
  }
}

# Secret for sensitive data
resource "kubernetes_secret" "orchestrator" {
  metadata {
    name      = "${local.app_name}-secrets"
    namespace = var.namespace
  }

  data = {
    redis-password = base64encode(var.redis_password)
    webhook-token  = base64encode("change-me-in-production")
    api-key        = base64encode("change-me-in-production")
  }

  type = "Opaque"
}

# Redis Deployment (if enabled)
resource "kubernetes_deployment" "redis" {
  count = var.redis_enabled ? 1 : 0

  metadata {
    name      = "redis"
    namespace = var.namespace
    labels = {
      app = "redis"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "redis"
      }
    }

    template {
      metadata {
        labels = {
          app = "redis"
        }
      }

      spec {
        container {
          name  = "redis"
          image = "redis:8-alpine"

          port {
            container_port = 6379
            name           = "redis"
          }

          command = ["redis-server"]
          args    = var.redis_password != "" ? ["--requirepass", var.redis_password] : []

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "256Mi"
            }
          }

          liveness_probe {
            tcp_socket {
              port = 6379
            }
            initial_delay_seconds = 30
            period_seconds        = 10
          }

          readiness_probe {
            exec {
              command = ["redis-cli", "ping"]
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }

          volume_mount {
            name       = "redis-data"
            mount_path = "/data"
          }
        }

        volume {
          name = "redis-data"
          empty_dir {}
        }
      }
    }
  }
}

# Redis Service
resource "kubernetes_service" "redis" {
  count = var.redis_enabled ? 1 : 0

  metadata {
    name      = "redis-service"
    namespace = var.namespace
    labels = {
      app = "redis"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      app = "redis"
    }

    port {
      port        = 6379
      target_port = 6379
      protocol    = "TCP"
      name        = "redis"
    }
  }
}

# Orchestrator Deployment
resource "kubernetes_deployment" "orchestrator" {
  metadata {
    name      = local.app_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    replicas = var.replicas

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = "1"
        max_unavailable = "0"
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
          "prometheus.io/port"   = "8000"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.orchestrator.metadata[0].name

        container {
          name              = local.app_name
          image             = local.image
          image_pull_policy = "IfNotPresent"

          port {
            container_port = 8000
            name           = "http"
            protocol       = "TCP"
          }

          env {
            name  = "PORT"
            value = "8000"
          }

          env {
            name = "REDIS_HOST"
            value_from {
              config_map_key_ref {
                name = kubernetes_config_map.orchestrator.metadata[0].name
                key  = "REDIS_HOST"
              }
            }
          }

          env {
            name = "REDIS_PORT"
            value_from {
              config_map_key_ref {
                name = kubernetes_config_map.orchestrator.metadata[0].name
                key  = "REDIS_PORT"
              }
            }
          }

          env {
            name = "MAX_RETRIES"
            value_from {
              config_map_key_ref {
                name = kubernetes_config_map.orchestrator.metadata[0].name
                key  = "MAX_RETRIES"
              }
            }
          }

          env {
            name = "ROLLBACK_THRESHOLD"
            value_from {
              config_map_key_ref {
                name = kubernetes_config_map.orchestrator.metadata[0].name
                key  = "ROLLBACK_THRESHOLD"
              }
            }
          }

          env {
            name = "LOG_LEVEL"
            value_from {
              config_map_key_ref {
                name = kubernetes_config_map.orchestrator.metadata[0].name
                key  = "LOG_LEVEL"
              }
            }
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
              port = 8000
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            timeout_seconds       = 3
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

  depends_on = [
    kubernetes_cluster_role_binding.orchestrator
  ]
}

# Orchestrator Service
resource "kubernetes_service" "orchestrator" {
  metadata {
    name      = "${local.app_name}-service"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    type = "ClusterIP"

    selector = {
      app = local.app_name
    }

    port {
      port        = 8000
      target_port = 8000
      protocol    = "TCP"
      name        = "http"
    }
  }
}
