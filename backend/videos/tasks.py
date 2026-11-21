import logging
from datetime import timedelta

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from videos.models import InvalidTransition, Video
from videos.uploads import discard_upload

logger = logging.getLogger(__name__)


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
