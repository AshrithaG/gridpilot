variable "cluster_name" {
  type = string
}

variable "node_port" {
  type = number
}

variable "image" {
  description = "Image to side-load into the cluster. kind pulls from a registry otherwise, and this image is local."
  type        = string
  default     = ""
}
