
output "production_namespace" {
  description = "Production namespace name"
  value       = kubernetes_namespace.production.metadata[0].name
}

output "staging_namespace" {
  description = "Staging namespace name"
  value       = kubernetes_namespace.staging.metadata[0].name
}

output "canary_namespace" {
  description = "Canary namespace name"
  value       = kubernetes_namespace.canary.metadata[0].name
}

output "orchestrator_namespace" {
  description = "Orchestrator namespace name"
  value       = kubernetes_namespace.orchestrator.metadata[0].name
}

output "monitoring_namespace" {
  description = "Monitoring namespace name"
  value       = kubernetes_namespace.monitoring.metadata[0].name
}
