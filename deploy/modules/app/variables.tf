variable "namespace" { type = string }
variable "image" { type = string }
variable "replicas" { type = number }
variable "node_port" { type = number }
variable "labels" { type = map(string) }
variable "cpu_limit" { type = string }
variable "memory_limit" { type = string }

variable "anthropic_api_key" {
  type      = string
  sensitive = true
}
