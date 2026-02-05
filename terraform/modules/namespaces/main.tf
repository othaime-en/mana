# Creates all required Kubernetes namespaces for the Mana project

resource "kubernetes_namespace" "production" {
  metadata {
    name = "production"
    labels = {
      name        = "production"
      environment = "production"
      managed-by  = "terraform"
    }
  }
}

resource "kubernetes_namespace" "staging" {
  metadata {
    name = "staging"
    labels = {
      name        = "staging"
      environment = "staging"
      managed-by  = "terraform"
    }
  }
}

resource "kubernetes_namespace" "canary" {
  metadata {
    name = "canary"
    labels = {
      name        = "canary"
      environment = "canary"
      managed-by  = "terraform"
    }
  }
}

resource "kubernetes_namespace" "orchestrator" {
  metadata {
    name = "orchestrator"
    labels = {
      name        = "orchestrator"
      environment = "production"
      managed-by  = "terraform"
    }
  }
}

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      name        = "monitoring"
      environment = "production"
      managed-by  = "terraform"
    }
  }
}
