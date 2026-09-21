output "environment" {
  value = local.env
}

output "url" {
  description = "Where the demo answers once the pods are ready."
  value       = "http://127.0.0.1:${var.node_port}"
}

output "health_check" {
  value = "curl -fsS http://127.0.0.1:${var.node_port}/api/state > /dev/null && echo serving"
}

output "cluster_name" {
  value = module.cluster.name
}

output "kubeconfig_path" {
  description = "kind writes a kubeconfig here; the drift test and kubectl both use it."
  value       = module.cluster.kubeconfig_path
}
