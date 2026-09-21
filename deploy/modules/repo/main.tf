# The one target here that is a real remote API rather than a local container.
# It is off by default: it needs a token with admin rights on a live repository,
# so enabling it should be a decision rather than a side effect of an apply.
terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

resource "github_branch_protection" "main" {
  count = var.enabled ? 1 : 0

  repository_id  = var.repository
  pattern        = "main"
  enforce_admins = false

  required_status_checks {
    strict   = true
    contexts = ["test"]
  }

  required_pull_request_reviews {
    required_approving_review_count = 0
    dismiss_stale_reviews           = true
  }
}
