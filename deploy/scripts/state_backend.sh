#!/usr/bin/env bash
# Remote state, self-hosted and free: a MinIO container speaking the S3 API.
#
# The Terraform side is the real s3 backend with native locking, so the only
# thing that changes on a move to AWS is the endpoint override in backend.hcl.
set -euo pipefail

NAME=${NAME:-gridpilot-tfstate}
PORT=${PORT:-19000}
CONSOLE_PORT=${CONSOLE_PORT:-19001}
KEY=${KEY:-gridpilot}
SECRET=${SECRET:-gridpilot-dev-secret}
BUCKET=${BUCKET:-gridpilot-tfstate}

case "${1:-up}" in
up)
  if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" \
      -p "127.0.0.1:${PORT}:9000" -p "127.0.0.1:${CONSOLE_PORT}:9001" \
      -e "MINIO_ROOT_USER=${KEY}" -e "MINIO_ROOT_PASSWORD=${SECRET}" \
      quay.io/minio/minio:latest server /data --console-address ":9001" >/dev/null
  fi
  # The bucket has to exist before init; MinIO does not create it on write.
  for _ in $(seq 1 30); do
    if docker run --rm --network host \
      -e "MC_HOST_local=http://${KEY}:${SECRET}@127.0.0.1:${PORT}" \
      quay.io/minio/mc:latest mb --ignore-existing "local/${BUCKET}" >/dev/null 2>&1; then
      echo "state bucket ready at http://127.0.0.1:${PORT}/${BUCKET}"
      exit 0
    fi
    sleep 1
  done
  echo "MinIO did not come up in 30s" >&2
  exit 1
  ;;
down)
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  echo "state backend stopped (the bucket and its contents go with it)"
  ;;
*)
  echo "usage: $0 [up|down]" >&2
  exit 2
  ;;
esac
