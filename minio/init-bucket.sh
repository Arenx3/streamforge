#!/bin/sh
# Creates the StreamForge bucket in MinIO. Idempotent: safe on every `up`.
set -eu

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER not set}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD not set}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set}"

attempt=0
until mc alias set local "http://minio:9000" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "[minio-init] MinIO not reachable after $attempt attempts" >&2
    exit 1
  fi
  echo "[minio-init] waiting for MinIO ($attempt)..."
  sleep 2
done

mc mb --ignore-existing "local/$MINIO_BUCKET"
echo "[minio-init] bucket '$MINIO_BUCKET' ready"
