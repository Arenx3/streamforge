"""Direct-to-S3 multipart uploads: browsers send the bytes to storage, never to Django."""

import math

from botocore.exceptions import ClientError
from django.conf import settings

from core.storage import s3_client, s3_public_client
from videos.models import Video

MIN_PART_BYTES = 16 * 1024 * 1024
MAX_PARTS = 1000
# Assembling a large object takes far longer than a normal S3 call.
COMPLETE_READ_TIMEOUT = 60


class UploadIncomplete(Exception):
    """Storage does not hold the full file the client declared."""


def split_into_parts(size_bytes):
    part_size = max(MIN_PART_BYTES, math.ceil(size_bytes / MAX_PARTS))
    count = math.ceil(size_bytes / part_size)
    return [part_size] * (count - 1) + [size_bytes - part_size * (count - 1)]


def start_upload(*, owner, title, description, content_type, size_bytes):
    video = Video(owner=owner, title=title, description=description, size_bytes=size_bytes)
    video.source_key = f"videos/{video.id}/source"
    video.upload_id = s3_client().create_multipart_upload(
        **_location(video), ContentType=content_type
    )["UploadId"]
    video.save()
    return video, _presigned_parts(video)


def finish_upload(video):
    """Assemble the uploaded parts and mark the video UPLOADED. Safe to retry."""
    s3 = s3_client(read_timeout=COMPLETE_READ_TIMEOUT)
    try:
        parts = _uploaded_parts(s3, video)
        s3.complete_multipart_upload(
            **_location(video), UploadId=video.upload_id, MultipartUpload={"Parts": parts}
        )
    except ClientError as error:
        # An earlier attempt may have assembled the object and lost the response.
        if _error_code(error) != "NoSuchUpload":
            raise

    try:
        size = s3.head_object(**_location(video))["ContentLength"]
    except ClientError as error:
        if _error_code(error) not in {"404", "NoSuchKey", "NotFound"}:
            raise
        raise UploadIncomplete("The upload no longer exists in storage.") from error
    if size != video.size_bytes:
        raise UploadIncomplete("The stored file does not match the declared size.")
    video.mark_uploaded(size_bytes=size)


def discard_upload(video):
    """Free everything a failed upload left in storage."""
    if not video.upload_id:
        return
    s3 = s3_client()
    try:
        s3.abort_multipart_upload(**_location(video), UploadId=video.upload_id)
    except ClientError as error:
        if _error_code(error) != "NoSuchUpload":
            raise
    s3.delete_object(**_location(video))
    Video.objects.filter(pk=video.pk).update(upload_id="")


def _presigned_parts(video):
    s3 = s3_public_client()
    expires_in = settings.VIDEO_UPLOAD_URL_EXPIRES
    sizes = split_into_parts(video.size_bytes)
    return {
        "part_size": sizes[0],
        "expires_in": expires_in,
        "parts": [
            {
                "part_number": number,
                "size": size,
                # Signing ContentLength makes storage reject a part of any other size,
                # so a client cannot exceed the limit it was validated against.
                "url": s3.generate_presigned_url(
                    "upload_part",
                    Params={
                        **_location(video),
                        "UploadId": video.upload_id,
                        "PartNumber": number,
                        "ContentLength": size,
                    },
                    ExpiresIn=expires_in,
                ),
            }
            for number, size in enumerate(sizes, start=1)
        ],
    }


def _uploaded_parts(s3, video):
    pages = s3.get_paginator("list_parts").paginate(**_location(video), UploadId=video.upload_id)
    parts = [part for page in pages for part in page.get("Parts", [])]
    numbers = [part["PartNumber"] for part in parts]
    if numbers != list(range(1, len(parts) + 1)):
        raise UploadIncomplete("Some parts have not been uploaded yet.")
    if sum(part["Size"] for part in parts) != video.size_bytes:
        raise UploadIncomplete("Some parts have not been uploaded yet.")
    return [{"PartNumber": part["PartNumber"], "ETag": part["ETag"]} for part in parts]


def _location(video):
    return {"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": video.source_key}


def _error_code(error):
    return error.response.get("Error", {}).get("Code", "")
