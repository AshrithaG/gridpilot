bucket = "gridpilot-tfstate"
key    = "gridpilot/terraform.tfstate"
region = "us-east-1"

endpoints = {
  s3 = "http://127.0.0.1:19000"
}

# Local-only credentials for a throwaway MinIO container bound to 127.0.0.1 by
# scripts/state_backend.sh. They protect nothing and are committed on purpose so
# the setup runs with one command; a real bucket takes them from the environment.
access_key = "gridpilot"
secret_key = "gridpilot-dev-secret"

use_lockfile                = true
use_path_style              = true
skip_credentials_validation = true
skip_metadata_api_check     = true
skip_region_validation      = true
skip_requesting_account_id  = true
