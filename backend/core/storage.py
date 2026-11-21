import logging
import random
import time

import boto3
from boto3.exceptions import Boto3Error
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)

STORAGE_ERRORS = (BotoCoreError, ClientError, Boto3Error)
S3_RETRIES = 3
S3_RETRY_BASE_SECONDS = 1


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


def is_transient(error):
    """A 4xx answer (missing object, bad credentials) will not fix itself on retry."""
    current = error
    while current is not None:
        if isinstance(current, ClientError):
            status = current.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 500)
            return status >= 500 or status in {408, 429}
        current = current.__cause__ or current.__context__
    return True


def with_retries(operation):
    """Run a storage call, retrying transient failures with exponential backoff."""
    for attempt in range(S3_RETRIES + 1):
        try:
            return operation()
        except STORAGE_ERRORS as error:
            if attempt == S3_RETRIES or not is_transient(error):
                raise
            delay = S3_RETRY_BASE_SECONDS * 2**attempt + random.uniform(0, 0.5)
            logger.warning("Storage call failed, retrying in %.1fs: %s", delay, error)
            time.sleep(delay)


def delete_prefix(s3, bucket, prefix):
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if not objects:
            continue
        response = s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        if response.get("Errors"):
            logger.warning("Could not delete %s objects in %s", len(response["Errors"]), prefix)
