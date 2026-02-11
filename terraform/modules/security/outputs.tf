output "production_network_policy" {
  description = "Name of the production network policy"
  value       = kubernetes_network_policy.sample_app.metadata[0].name
}

output "orchestrator_network_policy" {
  description = "Name of the orchestrator network policy"
  value       = kubernetes_network_policy.orchestrator.metadata[0].name
}
