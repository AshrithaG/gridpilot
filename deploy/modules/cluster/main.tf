# The cluster the app runs on. kind is a real Kubernetes API served by Docker
# containers, so everything above this module is the same code a managed cluster
# would take. What it cannot model is the cloud around it: no VPC, no IAM, no
# load balancer, no node pools.
terraform {
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.11"
    }
  }
}

resource "kind_cluster" "this" {
  name           = var.cluster_name
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"

      # A NodePort alone is not reachable from the host with kind: the port has
      # to be mapped out of the node container as well.
      extra_port_mappings {
        container_port = var.node_port
        host_port      = var.node_port
        listen_address = "127.0.0.1"
      }
    }
  }
}

# Terraform has no resource for "put this local image inside kind", so this is
# a provisioner, which is the documented escape hatch and stays an escape
# hatch: it is idempotent, it re-runs only when the cluster or the image
# changes, and nothing else in this configuration uses one.
resource "terraform_data" "load_image" {
  count = var.image == "" ? 0 : 1

  triggers_replace = [kind_cluster.this.name, var.image]

  provisioner "local-exec" {
    command = "kind load docker-image ${var.image} --name ${kind_cluster.this.name}"
  }
}
