#!/bin/sh
# Creates the StreamForge bucket on the local S3 server. Idempotent: safe on every `up`.
set -eu

: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID not set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY not set}"
: "${AWS_STORAGE_BUCKET_NAME:?AWS_STORAGE_BUCKET_NAME not set}"
: "${AWS_S3_ENDPOINT_URL:?AWS_S3_ENDPOINT_URL not set}"
export AWS_DEFAULT_REGION="${AWS_S3_REGION_NAME:-us-east-1}"

s3api() { aws --endpoint-url "$AWS_S3_ENDPOINT_URL" s3api "$@"; }

attempt=0
until s3api list-buckets >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "[storage-init] S3 server not reachable after $attempt attempts" >&2
    exit 1
  fi
  echo "[storage-init] waiting for S3 server ($attempt)..."
  sleep 2
done

if s3api head-bucket --bucket "$AWS_STORAGE_BUCKET_NAME" >/dev/null 2>&1; then
  echo "[storage-init] bucket '$AWS_STORAGE_BUCKET_NAME' already exists"
else
  s3api create-bucket --bucket "$AWS_STORAGE_BUCKET_NAME" >/dev/null
  echo "[storage-init] bucket '$AWS_STORAGE_BUCKET_NAME' created"
fi
