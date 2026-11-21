import logging
import shutil
from datetime import timedelta
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from core.storage import s3_client
from videos.models import InvalidTransition, Video
from videos.transcoding import (
    MASTER_PLAYLIST,
    TranscodeError,
    build_ffmpeg_command,
    estimated_output_bytes,
    plan_renditions,
    probe,
    require_free_space,
    run_ffmpeg,
    transcode_timeout,
)
from videos.uploads import discard_upload

logger = logging.getLogger(__name__)

HLS_CONTENT_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}


@shared_task(name="videos.abort_abandoned_uploads")
def abort_abandoned_uploads():
    """Fail uploads nobody finished and free their parts in storage."""
    cutoff = timezone.now() - timedelta(hours=settings.VIDEO_UPLOAD_ABANDON_HOURS)
    # FAILED rows that still hold an upload_id are retries after a storage error.
    stale = Video.objects.filter(
        Q(status=Video.Status.PENDING_UPLOAD, created_at__lt=cutoff)
        | (Q(status=Video.Status.FAILED) & ~Q(upload_id=""))
    )
    aborted = 0
    for video in stale.iterator():
        try:
            if video.status == Video.Status.PENDING_UPLOAD:
                video.mark_failed("Upload abandoned before it was completed.")
            discard_upload(video)
        except InvalidTransition:
            continue  # The client completed it in the meantime.
        except (BotoCoreError, ClientError):
            logger.exception("Could not abort the upload of video %s", video.pk)
            continue
        aborted += 1
    return {"aborted": aborted}


@shared_task(
    bind=True,
    name="videos.transcode_video",
    soft_time_limit=settings.VIDEO_TRANSCODE_MAX_SECONDS + 1800,
    time_limit=settings.VIDEO_TRANSCODE_MAX_SECONDS + 3600,
)
def transcode_video(self, video_id):
    """Turn an uploaded original into adaptive HLS. Safe to run again."""
    video = Video.objects.filter(pk=video_id).first()
    if video is None:
        return {"video": video_id, "skipped": True}

    workdir = Path(settings.STREAMFORGE_TMP_DIR) / str(video.id)
    if not video.claim_for_processing(self.request.id):
        # Only the task that owns the video may touch its directory.
        if video.processing_task_id == self.request.id:
            shutil.rmtree(workdir, ignore_errors=True)
        return {"video": video_id, "skipped": True}

    try:
        artifacts = _build_hls(video, workdir)
        close_old_connections()  # FFmpeg may have outlived the database connection.
        video.mark_ready(**artifacts)
    except InvalidTransition:
        logger.warning("Video %s changed state during processing", video.pk)
    except Exception as exc:
        logger.exception("Processing failed for video %s", video.pk)
        close_old_connections()
        _mark_failed(video, exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"video": video_id, "status": video.status}


def _build_hls(video, workdir):
    shutil.rmtree(workdir, ignore_errors=True)  # Leftovers from a crashed attempt.
    hls_dir = workdir / "hls"
    hls_dir.mkdir(parents=True)
    require_free_space(workdir, video.size_bytes or 0)

    s3 = s3_client()
    source = workdir / "source"
    s3.download_file(settings.AWS_STORAGE_BUCKET_NAME, video.source_key, str(source))

    media = probe(source)
    renditions = plan_renditions(media.width, media.height)
    require_free_space(workdir, estimated_output_bytes(renditions, media.duration))
    run_ffmpeg(
        build_ffmpeg_command(source, media, renditions),
        cwd=hls_dir,
        timeout=transcode_timeout(media.duration),
    )

    prefix = f"videos/{video.id}/hls"
    _upload_hls(s3, hls_dir, prefix)
    return {
        "hls_master_key": f"{prefix}/{MASTER_PLAYLIST}",
        "duration_seconds": media.duration,
        "width": media.width,
        "height": media.height,
    }


def _upload_hls(s3, hls_dir, prefix):
    files = [path for path in hls_dir.rglob("*") if path.suffix in HLS_CONTENT_TYPES]
    # Segments first and the master last, so no playlist ever points at a missing file.
    files.sort(key=lambda p: (p.suffix == ".m3u8", p.name == MASTER_PLAYLIST, p.as_posix()))
    for path in files:
        s3.upload_file(
            str(path),
            settings.AWS_STORAGE_BUCKET_NAME,
            f"{prefix}/{path.relative_to(hls_dir).as_posix()}",
            ExtraArgs={"ContentType": HLS_CONTENT_TYPES[path.suffix]},
        )


def _mark_failed(video, error):
    message = str(error) if isinstance(error, TranscodeError) else "Processing failed."
    try:
        video.mark_failed(message)
    except InvalidTransition:
        logger.warning("Video %s changed state before it could be marked failed", video.pk)
