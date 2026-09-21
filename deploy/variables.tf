variable "cluster_name" {
  description = "kind cluster name. Each workspace gets its own cluster so dev and prod cannot collide."
  type        = string
  default     = "gridpilot"
}

variable "image" {
  description = "Container image for the app, already built locally and loaded into the cluster."
  type        = string
  default     = "gridpilot:local"
}

variable "replicas" {
  description = "Pod replicas. The difference between workspaces lives here, not in a separate copy of the code."
  type        = number
  default     = 1
}

variable "node_port" {
  description = "Host port the demo is reachable on."
  type        = number
  default     = 30800
}

variable "cpu_limit" {
  type    = string
  default = "500m"
}

variable "memory_limit" {
  type    = string
  default = "512Mi"
}

variable "anthropic_api_key" {
  description = "Optional. Without it the agent paths stay off and the simulation still runs, which is how the public demo is configured."
  type        = string
  default     = ""
  sensitive   = true
}

variable "manage_github" {
  description = "Manage the repository's own branch protection as code. Off by default: it needs a token with admin rights on a real repository, so turning it on is a deliberate act."
  type        = bool
  default     = false
}

variable "github_repository" {
  type    = string
  default = "gridpilot"
}
