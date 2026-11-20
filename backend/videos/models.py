import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class InvalidTransition(Exception):
    """Raised when a video cannot move to the requested status."""


class Video(models.Model):
    class Status(models.TextChoices):
        PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
        UPLOADED = "UPLOADED", "Uploaded"
        PROCESSING = "PROCESSING", "Processing"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    # Allowed status changes. READY and FAILED are terminal.
    TRANSITIONS = {
        Status.PENDING_UPLOAD: {Status.UPLOADED, Status.FAILED},
        Status.UPLOADED: {Status.PROCESSING, Status.FAILED},
        Status.PROCESSING: {Status.READY, Status.FAILED},
        Status.READY: set(),
        Status.FAILED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Indexed through the (owner, -created_at) composite index below.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="videos",
        db_index=False,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING_UPLOAD)
    progress = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)

    # Object keys in the S3 bucket.
    source_key = models.CharField(max_length=512, blank=True)
    hls_master_key = models.CharField(max_length=512, blank=True)
    thumbnail_key = models.CharField(max_length=512, blank=True)

    duration_seconds = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="video_owner_created_idx"),
            models.Index(fields=["status"], name="video_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress__lte=100), name="video_progress_lte_100"
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.status})"

    def can_transition_to(self, status):
        return status in self.TRANSITIONS[self.status]

    def mark_uploaded(self, *, size_bytes=None):
        self._transition(self.Status.UPLOADED, size_bytes=size_bytes)

    def start_processing(self):
        self._transition(self.Status.PROCESSING, progress=0, error_message="")

    def mark_ready(
        self, *, hls_master_key, thumbnail_key="", duration_seconds=None, width=None, height=None
    ):
        self._transition(
            self.Status.READY,
            progress=100,
            hls_master_key=hls_master_key,
            thumbnail_key=thumbnail_key,
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            processed_at=timezone.now(),
        )

    def mark_failed(self, error_message):
        self._transition(self.Status.FAILED, error_message=error_message)

    def update_progress(self, progress):
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be between 0 and 100, got {progress}")
        if self.status != self.Status.PROCESSING:
            raise InvalidTransition(f"Cannot update progress of a {self.status} video")
        self._save_if_status_unchanged(progress=progress)

    def _transition(self, target, **changes):
        if not self.can_transition_to(target):
            raise InvalidTransition(f"Cannot move video from {self.status} to {target}")
        self._save_if_status_unchanged(status=target, **changes)

    def _save_if_status_unchanged(self, **changes):
        """Write `changes` only if the row still has the status this instance holds.

        The API and the workers can act on the same video at once (for example a
        redelivered Celery task). The conditional UPDATE makes the stale caller fail
        instead of silently overwriting the other one's work.
        """
        if self._state.adding:
            raise ValueError("Save the video before changing its status")
        changes["updated_at"] = timezone.now()
        updated = Video.objects.filter(pk=self.pk, status=self.status).update(**changes)
        if not updated:
            raise InvalidTransition(f"Video {self.pk} is no longer {self.status}")
        for field, value in changes.items():
            setattr(self, field, value)
