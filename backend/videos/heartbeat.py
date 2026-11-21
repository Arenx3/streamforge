"""Lets the pipeline tell a crashed run from one that is merely slow."""

import logging
import threading

from django.db import DatabaseError, connection
from django.utils import timezone

from videos.models import Video

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30


class Heartbeat:
    """Refreshes `heartbeat_at` from a background thread while a run is working."""

    def __init__(self, video):
        self._run_filter = {
            "pk": video.pk,
            "status": Video.Status.PROCESSING,
            "processing_attempts": video.processing_attempts,
        }
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc_info):
        self._stopped.set()
        self._thread.join(timeout=5)

    def _beat(self):
        try:
            while not self._stopped.wait(HEARTBEAT_INTERVAL_SECONDS):
                try:
                    Video.objects.filter(**self._run_filter).update(heartbeat_at=timezone.now())
                except DatabaseError:
                    logger.warning("Heartbeat failed for video %s", self._run_filter["pk"])
                    connection.close()
        finally:
            connection.close()  # Each thread owns its database connection.
