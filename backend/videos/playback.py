"""Private HLS playback: signed playlists that point at short-lived S3 URLs.

Players resolve playlist entries relative to the playlist URL, so a single presigned
link cannot cover a whole video. The token travels in the URL path, which keeps the
relative rendition links valid, and each segment gets its own presigned URL.
"""

import re

from botocore.exceptions import ClientError
from django.conf import settings
from django.core import signing
from django.urls import reverse

from core.storage import s3_client, s3_public_client
from videos.models import Video

_RENDITION = re.compile(r"\d{3,4}p")
_SEGMENT = re.compile(r"seg_\d+\.ts")


class PlaylistNotFound(Exception):
    """The requested playlist does not exist in storage."""


def _signer():
    return signing.TimestampSigner(salt="videos.playback", sep=".")


def make_playback_token(video):
    return _signer().sign(str(video.pk))


def read_playback_token(token):
    """Return the video id, or None if the token is forged or expired."""
    try:
        return _signer().unsign(token, max_age=settings.VIDEO_PLAYBACK_URL_EXPIRES)
    except signing.BadSignature:
        return None


def playback_url(request, video):
    if video.status != Video.Status.READY or not video.hls_master_key:
        return None
    path = reverse("video-playlist-master", args=[make_playback_token(video)])
    return request.build_absolute_uri(path)


def _share_signer():
    return signing.TimestampSigner(salt="videos.share-playback", sep=".")


def read_share_playback_token(token):
    """Return the video's share token, or None if the link is forged or expired."""
    try:
        return _share_signer().unsign(token, max_age=settings.VIDEO_SHARE_URL_EXPIRES)
    except signing.BadSignature:
        return None


def shared_playback_url(request, video):
    """A playback link that carries the share token, so revoking the share rejects it."""
    if not video.hls_master_key:
        return None
    path = reverse("shared-playlist-master", args=[_share_signer().sign(video.share_token)])
    return request.build_absolute_uri(path)


def presigned_get(s3, key, expires=None):
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": key},
        ExpiresIn=expires or settings.VIDEO_PLAYBACK_URL_EXPIRES,
    )


def master_playlist(video):
    return _read_playlist(video.hls_master_key)


def rendition_playlist(video, rendition, expires=None):
    """The rendition's playlist, with every segment replaced by a presigned URL."""
    if not _RENDITION.fullmatch(rendition):
        raise PlaylistNotFound(rendition)
    folder = f"{video.hls_master_key.rsplit('/', 1)[0]}/{rendition}"
    body = _read_playlist(f"{folder}/index.m3u8")
    s3 = s3_public_client()
    lines = [
        presigned_get(s3, f"{folder}/{line}", expires) if _SEGMENT.fullmatch(line) else line
        for line in body.splitlines()
    ]
    return "\n".join(lines) + "\n"


def _read_playlist(key):
    try:
        response = s3_client().get_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "NotFound", "404"}:
            raise PlaylistNotFound(key) from error
        raise
    return response["Body"].read().decode()
