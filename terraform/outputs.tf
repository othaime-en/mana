# Outputs
output "sample_app_service_url" {
  description = "Sample app service URL"
  value       = module.sample_app.service_url
}

output "orchestrator_url" {
  description = "Orchestrator API URL"
  value       = module.orchestrator.api_url
}

output "grafana_url" {
  description = "Grafana dashboard URL"
  value       = var.enable_monitoring ? module.monitoring.grafana_url : null
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = var.enable_monitoring ? module.monitoring.prometheus_url : null
}