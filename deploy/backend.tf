# Remote state with locking, self-hosted.
#
# There is no cloud budget for this project, so the state bucket is a MinIO
# container rather than S3. The backend block is the real s3 backend either way:
# the same configuration, the same locking, the same failure modes. Moving to S3
# means deleting the endpoint overrides and nothing else.
#
# use_lockfile is Terraform's native S3 locking, so no DynamoDB table is needed.
# Start the bucket with scripts/state_backend.sh, then:
#
#   terraform init -backend-config=backend.hcl
#
terraform {
  backend "s3" {}
}
