"""Live processing progress: frequent updates in Redis, occasional ones in PostgreSQL."""

import logging
import time

from django.core.cache import cache
from django.db import DatabaseError, close_old_connections
from redis.exceptions import RedisError

from videos.models import Video

logger = logging.getLogger(__name__)

# Share of the bar reached when each phase ends. 100 is reserved for READY.
DOWNLOADED = 5
TRANSCODED = 90
UPLOADED = 99

PUBLISH_INTERVAL_SECONDS = 1.0
PERSIST_STEP = 5
LIVE_TTL_SECONDS = 3600


def _live_key(video_id):
    return f"video-progress:{video_id}"


def live_progress(video):
    """Current progress, preferring the fresher Redis value while processing."""
    if video.status != Video.Status.PROCESSING:
        return video.progress
    try:
        live = cache.get(_live_key(video.pk))
    except RedisError:
        return video.progress
    return max(video.progress, live or 0)


class ProgressReporter:
    """Keeps progress monotonic and limits how often it is written."""

    def __init__(self, video, clock=time.monotonic):
        self.video = video
        self.progress = 0
        self._clock = clock
        self._persisted = 0
        self._last_publish = None
        self._publish(0)  # Overwrites whatever a crashed run left behind.

    def downloaded(self):
        self.report(DOWNLOADED)

    def transcoded(self, seconds, duration):
        ratio = min(seconds / duration, 1)
        self.report(DOWNLOADED + (TRANSCODED - DOWNLOADED) * ratio)

    def uploaded(self, done, total):
        self.report(TRANSCODED + (UPLOADED - TRANSCODED) * done / total)

    def report(self, percent):
        """Raises InvalidTransition if the video left PROCESSING, which stops the run."""
        percent = min(int(percent), UPLOADED)
        if percent <= self.progress:
            return
        self.progress = percent

        now = self._clock()
        if self._last_publish is None or now - self._last_publish >= PUBLISH_INTERVAL_SECONDS:
            self._last_publish = now
            self._publish(percent)
        if percent - self._persisted >= PERSIST_STEP:
            self._persisted = percent
            self._persist(percent)

    def finish(self):
        try:
            cache.delete(_live_key(self.video.pk))
        except RedisError:
            logger.warning("Could not clear live progress of video %s", self.video.pk)

    def _publish(self, percent):
        try:
            cache.set(_live_key(self.video.pk), percent, LIVE_TTL_SECONDS)
        except RedisError:
            logger.warning("Could not publish progress of video %s", self.video.pk)

    def _persist(self, percent):
        try:
            self.video.update_progress(percent)
        except DatabaseError:
            # A progress write is never worth failing a long transcode.
            logger.warning("Could not save progress of video %s", self.video.pk)
            close_old_connections()
