# Network policies, resource quotas, and limit ranges for all namespaces.
# Translates kubernetes/security/network-policies.yaml into Terraform-managed resources.

locals {
  dns_egress_ports = [
    { port = 53, protocol = "TCP" },
    { port = 53, protocol = "UDP" },
  ]
}

# ── Network Policies ──────────────────────────────────────────────────────────

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
          match_labels = { name = "ingress-nginx" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "5000"
      }
    }

    ingress {
      from {
        namespace_selector {
          match_labels = { name = "monitoring" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "5000"
      }
    }

    # DNS egress
    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { name = "kube-system" }
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
          match_labels = { name = "ingress-nginx" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "5000"
      }
    }

    ingress {
      from {
        namespace_selector {
          match_labels = { name = "monitoring" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "5000"
      }
    }

    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { name = "kube-system" }
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

    # Accept webhook calls from anywhere (GitHub Actions, in-cluster callers)
    ingress {
      from {
        namespace_selector {}
      }
      ports {
        protocol = "TCP"
        port     = "8000"
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

    # Kubernetes API server
    egress {
      to {
        namespace_selector {
          match_labels = { name = "kube-system" }
        }
      }
      ports {
        protocol = "TCP"
        port     = "443"
      }
    }

    dynamic "egress" {
      for_each = local.dns_egress_ports
      content {
        to {
          namespace_selector {
            match_labels = { name = "kube-system" }
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
