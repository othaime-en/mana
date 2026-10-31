# Network policies, resource quotas, and limit ranges for all namespaces.
# Mirrors kubernetes/security/network-policies.yaml and resource-quotas.yaml.
# Namespaces are selected with kubernetes.io/metadata.name, which Kubernetes sets on
# every namespace. Enforcement needs a CNI that implements NetworkPolicy.

variable "ci_runner_namespace" {
  description = "Namespace whose pods may call the orchestrator (for example Actions Runner Controller runners)"
  type        = string
  default     = "ci-runners"
}

variable "managed_namespaces" {
  description = "Namespaces whose sample-app pods the orchestrator probes and patches"
  type        = list(string)
  default     = ["production", "staging", "canary"]
}

locals {
  dns_egress_ports = [
    { port = 53, protocol = "TCP" },
    { port = 53, protocol = "UDP" },
  ]
  apiserver_egress_ports = [443, 6443]
  sample_app_port        = "5000"
}

# ── Network Policies ──────────────────────────────────────────────────────────

resource "kubernetes_network_policy" "default_deny_production" {
  metadata {
    name      = "default-deny-all"
    namespace = "production"
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy" "default_deny_staging" {
  metadata {
    name      = "default-deny-all"
    namespace = "staging"
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy" "default_deny_orchestrator" {
  metadata {
    name      = "default-deny-all"
    namespace = "orchestrator"
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy" "sample_app" {
  metadata {
    name      = "sample-app-netpol"
    namespace = "production"
  }

  spec {
    pod_selector {
      match_labels = { app = "sample-app" }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "orchestrator" }
        }
        pod_selector {
          match_labels = { app = "orchestrator" }
        }
      }
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "monitoring" }
        }
      }
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "ingress-nginx" }
        }
      }
      ports {
        protocol = "TCP"
        port     = local.sample_app_port
      }
    }

    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { "kubernetes.io/metadata.name" = "kube-system" }
          }
          pod_selector {
            match_labels = { "k8s-app" = "kube-dns" }
          }
        }
        ports {
          protocol = egress.value.protocol
          port     = tostring(egress.value.port)
        }
      }
    }
  }
}

resource "kubernetes_network_policy" "sample_app_staging" {
  metadata {
    name      = "sample-app-netpol"
    namespace = "staging"
  }

  spec {
    pod_selector {
      match_labels = { app = "sample-app" }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "orchestrator" }
        }
        pod_selector {
          match_labels = { app = "orchestrator" }
        }
      }
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "monitoring" }
        }
      }
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "ingress-nginx" }
        }
      }
      ports {
        protocol = "TCP"
        port     = local.sample_app_port
      }
    }

    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { "kubernetes.io/metadata.name" = "kube-system" }
          }
          pod_selector {
            match_labels = { "k8s-app" = "kube-dns" }
          }
        }
        ports {
          protocol = egress.value.protocol
          port     = tostring(egress.value.port)
        }
      }
    }
  }
}

resource "kubernetes_network_policy" "orchestrator" {
  metadata {
    name      = "orchestrator-netpol"
    namespace = "orchestrator"
  }

  spec {
    pod_selector {
      match_labels = { app = "orchestrator" }
    }

    policy_types = ["Ingress", "Egress"]

    # Prometheus scrapes /metrics; the pipeline posts webhooks from its runner namespace
    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "monitoring" }
        }
      }
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = var.ci_runner_namespace }
        }
      }
      ports {
        protocol = "TCP"
        port     = "8000"
      }
    }

    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { "kubernetes.io/metadata.name" = "kube-system" }
          }
          pod_selector {
            match_labels = { "k8s-app" = "kube-dns" }
          }
        }
        ports {
          protocol = egress.value.protocol
          port     = tostring(egress.value.port)
        }
      }
    }

    egress {
      to {
        pod_selector {
          match_labels = { app = "redis" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "6379"
      }
    }

    # Health probes against the managed applications
    egress {
      to {
        namespace_selector {
          match_expressions {
            key      = "kubernetes.io/metadata.name"
            operator = "In"
            values   = var.managed_namespaces
          }
        }
        pod_selector {
          match_labels = { app = "sample-app" }
        }
      }
      ports {
        protocol = "TCP"
        port     = local.sample_app_port
      }
    }

    # Kubernetes API server and api.github.com; narrow the CIDR to the API server address when known
    dynamic "egress" {
      for_each = local.apiserver_egress_ports
      content {
        to {
          ip_block {
            cidr = "0.0.0.0/0"
          }
        }
        ports {
          protocol = "TCP"
          port     = tostring(egress.value)
        }
      }
    }
  }
}

resource "kubernetes_network_policy" "redis" {
  metadata {
    name      = "redis-netpol"
    namespace = "orchestrator"
  }

  spec {
    pod_selector {
      match_labels = { app = "redis" }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      from {
        pod_selector {
          match_labels = { app = "orchestrator" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "6379"
      }
    }
  }
}

# ── Resource Quotas ───────────────────────────────────────────────────────────

resource "kubernetes_resource_quota" "production" {
  metadata {
    name      = "production-quota"
    namespace = "production"
  }

  spec {
    hard = {
      "requests.cpu"    = "10"
      "requests.memory" = "20Gi"
      "limits.cpu"      = "20"
      "limits.memory"   = "40Gi"
      "pods"            = "50"
    }
  }
}

resource "kubernetes_resource_quota" "staging" {
  metadata {
    name      = "staging-quota"
    namespace = "staging"
  }

  spec {
    hard = {
      "requests.cpu"    = "4"
      "requests.memory" = "8Gi"
      "limits.cpu"      = "8"
      "limits.memory"   = "16Gi"
      "pods"            = "20"
    }
  }
}

resource "kubernetes_resource_quota" "orchestrator" {
  metadata {
    name      = "orchestrator-quota"
    namespace = "orchestrator"
  }

  spec {
    hard = {
      "requests.cpu"    = "2"
      "requests.memory" = "4Gi"
      "limits.cpu"      = "4"
      "limits.memory"   = "8Gi"
      "pods"            = "10"
    }
  }
}

# ── Limit Ranges ──────────────────────────────────────────────────────────────

resource "kubernetes_limit_range" "production" {
  metadata {
    name      = "production-limits"
    namespace = "production"
  }

  spec {
    limit {
      type = "Container"
      max = {
        cpu    = "2"
        memory = "4Gi"
      }
      min = {
        cpu    = "50m"
        memory = "64Mi"
      }
      default = {
        cpu    = "200m"
        memory = "256Mi"
      }
      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }
    }
  }
}

resource "kubernetes_limit_range" "staging" {
  metadata {
    name      = "staging-limits"
    namespace = "staging"
  }

  spec {
    limit {
      type = "Container"
      max = {
        cpu    = "1"
        memory = "2Gi"
      }
      min = {
        cpu    = "50m"
        memory = "64Mi"
      }
      default = {
        cpu    = "100m"
        memory = "128Mi"
      }
      default_request = {
        cpu    = "50m"
        memory = "64Mi"
      }
    }
  }
}

resource "kubernetes_limit_range" "orchestrator" {
  metadata {
    name      = "orchestrator-limits"
    namespace = "orchestrator"
  }

  spec {
    limit {
      type = "Container"
      max = {
        cpu    = "1"
        memory = "1Gi"
      }
      min = {
        cpu    = "50m"
        memory = "64Mi"
      }
      default = {
        cpu    = "200m"
        memory = "256Mi"
      }
      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }
    }
  }
}
