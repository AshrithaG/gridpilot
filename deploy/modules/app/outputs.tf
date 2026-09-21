output "namespace" {
  value = kubernetes_namespace.this.metadata[0].name
}

output "deployment" {
  value = kubernetes_deployment.this.metadata[0].name
}
