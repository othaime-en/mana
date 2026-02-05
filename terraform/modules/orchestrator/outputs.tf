
output "deployment_name" {
  description = "Name of the orchestrator deployment"
  value       = kubernetes_deployment.orchestrator.metadata[0].name
}

output "service_name" {
  description = "Name of the orchestrator service"
  value       = kubernetes_service.orchestrator.metadata[0].name
}

output "api_url" {
  description = "Orchestrator API URL (for port-forwarding)"
  value       = "kubectl port-forward -n ${var.namespace} svc/${kubernetes_service.orchestrator.metadata[0].name} 8000:8000"
}

output "namespace" {
  description = "Namespace where orchestrator is deployed"
  value       = var.namespace
}

output "redis_service_name" {
  description = "Name of the Redis service (if deployed)"
  value       = var.redis_enabled ? kubernetes_service.redis[0].metadata[0].name : null
}

output "service_account_name" {
  description = "Name of the service account"
  value       = kubernetes_service_account.orchestrator.metadata[0].name
}
