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

# Namespaced access: only the managed namespaces, only the verbs the code uses
resource "kubernetes_role" "orchestrator" {
  for_each = toset(var.managed_namespaces)

  metadata {
    name      = "mana-orchestrator"
    namespace = each.value
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments"]
    verbs      = ["get", "list", "patch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["replicasets"]
    verbs      = ["list"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["list"]
  }
}

resource "kubernetes_role_binding" "orchestrator" {
  for_each = toset(var.managed_namespaces)

  metadata {
    name      = "mana-orchestrator"
    namespace = each.value
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.orchestrator[each.value].metadata[0].name
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
    MAX_RETRIES = tostring(var.max_retries)
    REDIS_HOST  = var.redis_enabled ? "redis-service" : "redis"
    REDIS_PORT  = "6379"
    LOG_LEVEL   = "INFO"
  }
}

# The recovery policy; the manifest orchestrator/k8s/policy-configmap.yaml is the single source
resource "kubernetes_config_map" "policy" {
  metadata {
    name      = "mana-policy"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    "policy.yaml" = yamldecode(file("${path.module}/../../../orchestrator/k8s/policy-configmap.yaml")).data["policy.yaml"]
  }
}

resource "random_password" "redis" {
  length  = 48
  special = false
}

resource "random_password" "webhook" {
  length  = 48
  special = false
}

resource "random_password" "api_key" {
  length  = 48
  special = false
}

# Secret for sensitive data (the provider base64-encodes data itself)
resource "kubernetes_secret" "orchestrator" {
  metadata {
    name      = "${local.app_name}-secrets"
    namespace = var.namespace
  }

  data = {
    redis-password = random_password.redis.result
    webhook-secret = random_password.webhook.result
    api-key        = random_password.api_key.result
  }

  type = "Opaque"
}

# Redis Deployment (if enabled)
resource "kubernetes_stateful_set_v1" "redis" {
  count = var.redis_enabled ? 1 : 0

  metadata {
    name      = "redis"
    namespace = var.namespace
    labels = {
      app = "redis"
    }
  }

  spec {
    replicas     = 1
    service_name = kubernetes_service.redis_headless[0].metadata[0].name

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
        automount_service_account_token  = false
        termination_grace_period_seconds = 30

        security_context {
          run_as_non_root = true
          run_as_user     = 999
          run_as_group    = 999
          fs_group        = 999
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name  = "redis"
          image = "redis:8-alpine"

          port {
            container_port = 6379
            name           = "redis"
          }

          command = ["sh", "-c", "exec redis-server --dir /data --appendonly yes --appendfsync everysec --save \"\" --requirepass \"$REDIS_PASSWORD\""]

          env {
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.orchestrator.metadata[0].name
                key  = "redis-password"
              }
            }
          }

          env {
            name = "REDISCLI_AUTH"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.orchestrator.metadata[0].name
                key  = "redis-password"
              }
            }
          }

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

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }
        }

      }
    }

    volume_claim_template {
      metadata {
        name = "redis-data"
        labels = {
          app = "redis"
        }
      }

      spec {
        access_modes = ["ReadWriteOnce"]

        resources {
          requests = {
            storage = var.redis_storage_size
          }
        }
      }
    }
  }
}

# Headless Service the StatefulSet is governed by
resource "kubernetes_service" "redis_headless" {
  count = var.redis_enabled ? 1 : 0

  metadata {
    name      = "redis-headless"
    namespace = var.namespace
    labels = {
      app = "redis"
    }
  }

  spec {
    cluster_ip = "None"

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
        service_account_name             = kubernetes_service_account.orchestrator.metadata[0].name
        termination_grace_period_seconds = 45

        topology_spread_constraint {
          max_skew           = 1
          topology_key       = "kubernetes.io/hostname"
          when_unsatisfiable = "ScheduleAnyway"
          label_selector {
            match_labels = {
              app = local.app_name
            }
          }
        }

        security_context {
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

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
            name  = "JOB_LEASE_SECONDS"
            value = "60"
          }

          env {
            name  = "SHUTDOWN_GRACE_SECONDS"
            value = "30"
          }

          env {
            name  = "POLICY_FILE"
            value = "/etc/mana/policy.yaml"
          }

          env {
            name  = "PROMETHEUS_URL"
            value = var.prometheus_url
          }

          env {
            name  = "WATCHER_ENABLED"
            value = tostring(var.watcher_enabled)
          }

          env {
            name  = "WATCHER_NAMESPACES"
            value = join(",", var.watcher_namespaces)
          }

          env {
            name = "SLACK_WEBHOOK_URL"
            value_from {
              secret_key_ref {
                name     = "slack-webhook"
                key      = "url"
                optional = true
              }
            }
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
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.orchestrator.metadata[0].name
                key  = "redis-password"
              }
            }
          }

          env {
            name = "WEBHOOK_SECRET"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.orchestrator.metadata[0].name
                key  = "webhook-secret"
              }
            }
          }

          env {
            name = "API_KEY"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.orchestrator.metadata[0].name
                key  = "api-key"
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

          startup_probe {
            http_get {
              path = "/livez"
              port = 8000
            }
            period_seconds    = 5
            failure_threshold = 24
          }

          liveness_probe {
            http_get {
              path = "/livez"
              port = 8000
            }
            period_seconds    = 10
            timeout_seconds   = 3
            failure_threshold = 3
          }

          readiness_probe {
            http_get {
              path = "/readyz"
              port = 8000
            }
            period_seconds    = 5
            timeout_seconds   = 3
            failure_threshold = 3
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

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }

          volume_mount {
            name       = "policy"
            mount_path = "/etc/mana"
            read_only  = true
          }
        }

        volume {
          name = "tmp"
          empty_dir {
            size_limit = "64Mi"
          }
        }

        volume {
          name = "policy"
          config_map {
            name = kubernetes_config_map.policy.metadata[0].name
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_role_binding.orchestrator,
    kubernetes_config_map.policy
  ]
}

resource "kubernetes_pod_disruption_budget_v1" "orchestrator" {
  metadata {
    name      = "${local.app_name}-pdb"
    namespace = var.namespace
  }

  spec {
    max_unavailable = "1"

    selector {
      match_labels = {
        app = local.app_name
      }
    }
  }
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
