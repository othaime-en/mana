
output "deployment_name" {
  description = "Name of the deployment"
  value       = kubernetes_deployment.sample_app.metadata[0].name
}

output "service_name" {
  description = "Name of the service"
  value       = kubernetes_service.sample_app.metadata[0].name
}

output "service_url" {
  description = "Service URL (for port-forwarding)"
  value       = "kubectl port-forward -n ${var.namespace} svc/${kubernetes_service.sample_app.metadata[0].name} 8080:80"
}

output "ingress_host" {
  description = "Ingress host"
  value       = var.ingress_host
}

output "namespace" {
  description = "Namespace where app is deployed"
  value       = var.namespace
}
