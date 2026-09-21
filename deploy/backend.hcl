bucket = "gridpilot-tfstate"
key    = "gridpilot/terraform.tfstate"
region = "us-east-1"

endpoints = {
  s3 = "http://127.0.0.1:19000"
}

access_key = "gridpilot"
secret_key = "gridpilot-dev-secret"

use_lockfile                = true
use_path_style              = true
skip_credentials_validation = true
skip_metadata_api_check     = true
skip_region_validation      = true
skip_requesting_account_id  = true
