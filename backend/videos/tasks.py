import contextlib
import logging
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from core.storage import STORAGE_ERRORS, delete_prefix, s3_client, with_retries
from videos.heartbeat import Heartbeat
from videos.models import InvalidTransition, Video
from videos.progress import ProgressReporter
from videos.transcoding import (
    MASTER_PLAYLIST,
    THUMBNAIL_NAME,
    TranscodeError,
    build_ffmpeg_command,
    estimated_output_bytes,
    extract_thumbnail,
    plan_renditions,
    probe,
    require_free_space,
    run_ffmpeg,
    transcode_timeout,
)
from videos.uploads import abort_multipart, discard_upload

logger = logging.getLogger(__name__)

HLS_CONTENT_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}
RECOVERY_COOLDOWN_SECONDS = 600


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

    video_dir = Path(settings.STREAMFORGE_TMP_DIR) / str(video.id)
    if not video.claim_for_processing(self.request.id):
        # A run that gave up cleans after itself; a live one keeps its files.
        if video.status == Video.Status.FAILED and video.processing_task_id == self.request.id:
            shutil.rmtree(video_dir, ignore_errors=True)
            _discard_artifacts(video)
        return {"video": video_id, "skipped": True}

    # One directory per attempt, so a stale run can never touch a newer run's files.
    workdir = video_dir / str(video.processing_attempts)
    reporter = ProgressReporter(video)
    try:
        with Heartbeat(video):
            artifacts = _build_artifacts(video, workdir, reporter)
        close_old_connections()  # FFmpeg may have outlived the database connection.
        video.mark_ready(**artifacts)
    except InvalidTransition:
        logger.warning("Video %s changed state during processing", video.pk)
        close_old_connections()
        _discard_if_abandoned(video)
    except Exception as exc:
        logger.exception("Processing failed for video %s", video.pk)
        close_old_connections()
        _mark_failed(video, exc)
    finally:
        reporter.finish()
        _remove_workdir(workdir)
    return {"video": video_id, "status": video.status}


@shared_task(
    name="videos.delete_video_files",
    autoretry_for=STORAGE_ERRORS,
    retry_backoff=30,
    retry_backoff_max=3600,
    retry_jitter=True,
    max_retries=8,
)
def delete_video_files(video_id, upload_id=""):
    """Remove everything a deleted video had in storage."""
    prefix = f"videos/{uuid.UUID(video_id)}/"  # A strict id keeps the prefix to this video.
    s3 = s3_client()
    if upload_id:
        abort_multipart(s3, f"{prefix}source", upload_id)
    delete_prefix(s3, settings.AWS_STORAGE_BUCKET_NAME, prefix)


@shared_task(name="videos.recover_stuck_videos")
def recover_stuck_videos():
    """Restart videos whose worker died, instead of waiting for Redis to redeliver them."""
    cutoff = timezone.now() - timedelta(seconds=settings.VIDEO_STUCK_AFTER_SECONDS)
    crashed = (
        Q(status=Video.Status.PROCESSING)
        & ~Q(processing_task_id="")
        & (Q(heartbeat_at__lt=cutoff) | Q(heartbeat_at__isnull=True))
    )
    message_lost = Q(status=Video.Status.UPLOADED, updated_at__lt=cutoff)
    recovered = 0
    for video in Video.objects.filter(crashed | message_lost).iterator():
        # Claims ignore duplicates; the cooldown only avoids flooding a busy queue.
        if cache.add(f"video-recovery:{video.pk}", 1, RECOVERY_COOLDOWN_SECONDS):
            transcode_video.apply_async(
                args=[str(video.pk)], task_id=video.processing_task_id or None
            )
            recovered += 1
    return {"recovered": recovered, "orphaned_dirs_removed": _remove_orphaned_workdirs()}


def _remove_orphaned_workdirs():
    base = Path(settings.STREAMFORGE_TMP_DIR)
    if not base.is_dir():
        return 0
    candidates = {path.name: path for path in base.iterdir() if _is_video_dir(path)}
    running = Video.objects.filter(pk__in=candidates, status=Video.Status.PROCESSING)
    active = {str(pk) for pk in running.values_list("pk", flat=True)}
    orphaned = [path for name, path in candidates.items() if name not in active]
    for path in orphaned:
        shutil.rmtree(path, ignore_errors=True)
    return len(orphaned)


def _is_video_dir(path):
    if not path.is_dir():
        return False
    try:
        uuid.UUID(path.name)
    except ValueError:
        return False
    return True


def _remove_workdir(workdir):
    shutil.rmtree(workdir, ignore_errors=True)
    with contextlib.suppress(OSError):
        workdir.parent.rmdir()  # Stays if another attempt is still using it.


def _build_artifacts(video, workdir, reporter):
    shutil.rmtree(workdir.parent, ignore_errors=True)  # Leftovers from crashed attempts.
    hls_dir = workdir / "hls"
    hls_dir.mkdir(parents=True)
    require_free_space(workdir, video.size_bytes or 0)

    s3 = s3_client()
    source = workdir / "source"
    with_retries(
        lambda: s3.download_file(settings.AWS_STORAGE_BUCKET_NAME, video.source_key, str(source))
    )
    reporter.downloaded()

    media = probe(source)
    renditions = plan_renditions(media.width, media.height)
    require_free_space(workdir, estimated_output_bytes(renditions, media.duration))
    run_ffmpeg(
        build_ffmpeg_command(source, media, renditions),
        cwd=hls_dir,
        timeout=transcode_timeout(media.duration),
        on_progress=lambda seconds: reporter.transcoded(seconds, media.duration),
    )

    prefix = f"videos/{video.id}/hls"
    _upload_hls(s3, hls_dir, prefix, reporter)
    thumbnail_key = _publish_thumbnail(s3, video, source, workdir, media.duration)
    return {
        "hls_master_key": f"{prefix}/{MASTER_PLAYLIST}",
        "thumbnail_key": thumbnail_key,
        "duration_seconds": media.duration,
        "width": media.width,
        "height": media.height,
    }


def _upload_hls(s3, hls_dir, prefix, reporter):
    files = [path for path in hls_dir.rglob("*") if path.suffix in HLS_CONTENT_TYPES]
    # Segments first and the master last, so no playlist ever points at a missing file.
    files.sort(key=lambda p: (p.suffix == ".m3u8", p.name == MASTER_PLAYLIST, p.as_posix()))
    for done, path in enumerate(files, start=1):
        key = f"{prefix}/{path.relative_to(hls_dir).as_posix()}"
        _upload(s3, path, key, HLS_CONTENT_TYPES[path.suffix])
        reporter.uploaded(done, len(files))


def _publish_thumbnail(s3, video, source, workdir, duration):
    """A missing cover must not throw away a finished transcode."""
    target = workdir / "thumbnail" / THUMBNAIL_NAME
    key = f"videos/{video.id}/{THUMBNAIL_NAME}"
    try:
        if not extract_thumbnail(source, target, duration):
            logger.warning("No thumbnail could be extracted for video %s", video.pk)
            return ""
        _upload(s3, target, key, "image/webp")
    except (OSError, *STORAGE_ERRORS):
        logger.warning("Could not publish the thumbnail of video %s", video.pk, exc_info=True)
        return ""
    return key


def _upload(s3, path, key, content_type):
    with_retries(
        lambda: s3.upload_file(
            str(path),
            settings.AWS_STORAGE_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    )


def _failure_message(error):
    if isinstance(error, TranscodeError):
        return str(error)
    if isinstance(error, SoftTimeLimitExceeded):
        return "Processing took too long and was stopped."
    if isinstance(error, STORAGE_ERRORS):
        return "Storage was unavailable while processing this video."
    return "Processing failed."


def _mark_failed(video, error):
    try:
        video.mark_failed(_failure_message(error))
    except InvalidTransition:
        logger.warning("Video %s changed state before it could be marked failed", video.pk)
        return
    _discard_artifacts(video)


def _discard_if_abandoned(video):
    """Keeps the files if another run took over: they may be the ones it is publishing."""
    current = Video.objects.filter(pk=video.pk).values_list("status", flat=True).first()
    if current in (None, Video.Status.FAILED):
        _discard_artifacts(video)


def _discard_artifacts(video):
    """Partial output must not outlive a failed run. The original is kept."""
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    prefix = f"videos/{video.id}"
    s3 = s3_client()
    try:
        with_retries(lambda: delete_prefix(s3, bucket, f"{prefix}/hls/"))
        with_retries(lambda: s3.delete_object(Bucket=bucket, Key=f"{prefix}/{THUMBNAIL_NAME}"))
    except STORAGE_ERRORS:
        logger.exception("Could not remove the partial files of video %s", video.pk)
