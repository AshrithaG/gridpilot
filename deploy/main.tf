# One root module, two workspaces. The workspace decides the cluster name and
# the size of the deployment; everything else is identical, which is the point
# of using workspaces rather than a second copy of the code.
locals {
  env = terraform.workspace == "default" ? "dev" : terraform.workspace

  labels = {
    "app.kubernetes.io/name"       = "gridpilot"
    "app.kubernetes.io/managed-by" = "terraform"
    "gridpilot.env"                = local.env
  }
}

module "cluster" {
  source       = "./modules/cluster"
  cluster_name = "${var.cluster_name}-${local.env}"
  node_port    = var.node_port
  image        = var.image
}

provider "kubernetes" {
  host                   = module.cluster.endpoint
  client_certificate     = module.cluster.client_certificate
  client_key             = module.cluster.client_key
  cluster_ca_certificate = module.cluster.cluster_ca_certificate
}

module "app" {
  source = "./modules/app"

  namespace         = "gridpilot"
  image             = var.image
  replicas          = var.replicas
  node_port         = var.node_port
  labels            = local.labels
  cpu_limit         = var.cpu_limit
  memory_limit      = var.memory_limit
  anthropic_api_key = var.anthropic_api_key

  depends_on = [module.cluster]
}

module "repo" {
  source     = "./modules/repo"
  enabled    = var.manage_github
  repository = var.github_repository
}
