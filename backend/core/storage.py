import boto3
from botocore.config import Config
from django.conf import settings


def s3_client(*, endpoint_url=None, read_timeout=10):
    """boto3 client for local RustFS or Cloudflare R2 (prod), configured from env."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=read_timeout,
            retries={"max_attempts": 2},
        ),
    )


def s3_public_client():
    """Signs browser URLs: the signature covers the host, so it must be the public one."""
    return s3_client(endpoint_url=settings.AWS_S3_PUBLIC_ENDPOINT_URL)
