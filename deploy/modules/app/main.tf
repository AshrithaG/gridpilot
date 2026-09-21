# The workload: namespace, configuration, the optional API key, the deployment
# and the service that exposes it.
terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
  }
}

resource "kubernetes_namespace" "this" {
  metadata {
    name   = var.namespace
    labels = var.labels
  }
}

resource "kubernetes_config_map" "this" {
  metadata {
    name      = "gridpilot-config"
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = var.labels
  }

  data = {
    # Everything that writes has to point at the mounted volume, because the
    # container runs with a read-only root filesystem as a non-root user.
    # matplotlib is the one that catches people out: pandapower imports it, and
    # it insists on a writable config and cache directory at import time, so
    # without MPLCONFIGDIR the pod crashes on startup rather than on first plot.
    GRIDPILOT_COUNTS = "/scratch/gridpilot_counts.json"
    PORT             = "8000"
    HOME             = "/scratch"
    MPLCONFIGDIR     = "/scratch/matplotlib"
    XDG_CONFIG_HOME  = "/scratch/.config"
    XDG_CACHE_HOME   = "/scratch/.cache"
  }
}

resource "kubernetes_secret" "api_key" {
  metadata {
    name      = "gridpilot-api-key"
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = var.labels
  }

  data = {
    # Empty is a supported state, not a missing value: without a key the agent
    # paths stay off and the simulation still runs.
    ANTHROPIC_API_KEY = var.anthropic_api_key
  }

  type = "Opaque"
}

resource "kubernetes_deployment" "this" {
  metadata {
    name      = "gridpilot"
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = var.labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = { "app.kubernetes.io/name" = var.labels["app.kubernetes.io/name"] }
    }

    template {
      metadata {
        labels = var.labels
      }

      spec {
        container {
          name              = "gridpilot"
          image             = var.image
          image_pull_policy = "IfNotPresent"

          port {
            container_port = 8000
            name           = "http"
          }

          env_from {
            config_map_ref { name = kubernetes_config_map.this.metadata[0].name }
          }

          env {
            name = "ANTHROPIC_API_KEY"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.api_key.metadata[0].name
                key  = "ANTHROPIC_API_KEY"
              }
            }
          }

          resources {
            limits = {
              cpu    = var.cpu_limit
              memory = var.memory_limit
            }
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
          }

          # The app's own health endpoint, the same one Render checks.
          readiness_probe {
            http_get {
              path = "/api/state"
              port = 8000
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }

          liveness_probe {
            http_get {
              path = "/api/state"
              port = 8000
            }
            initial_delay_seconds = 20
            period_seconds        = 20
          }

          volume_mount {
            name       = "scratch"
            mount_path = "/scratch"
          }

          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            run_as_user                = 1000
            allow_privilege_escalation = false
          }
        }

        volume {
          name = "scratch"
          empty_dir {}
        }
      }
    }
  }
}

resource "kubernetes_service" "this" {
  metadata {
    name      = "gridpilot"
    namespace = kubernetes_namespace.this.metadata[0].name
    labels    = var.labels
  }

  spec {
    type     = "NodePort"
    selector = { "app.kubernetes.io/name" = var.labels["app.kubernetes.io/name"] }

    port {
      port        = 80
      target_port = 8000
      node_port   = var.node_port
    }
  }
}
