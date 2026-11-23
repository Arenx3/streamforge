"""Live video updates: workers publish to Redis and the SSE endpoint relays them to the owner."""

import json
import logging
import time

import redis
from django.conf import settings
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15
POLL_SECONDS = 1.0

_publisher = None


def channel_for(owner_id):
    return f"video-events:{owner_id}"


def _client():
    global _publisher
    if _publisher is None:
        _publisher = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
    return _publisher


def _publish(owner_id, payload):
    """Best effort: a missed update must never interrupt the work it describes."""
    try:
        _client().publish(channel_for(owner_id), json.dumps(payload))
    except RedisError:
        logger.warning("Could not publish a live update for video %s", payload["id"])


def publish_video_event(video, kind, progress=None):
    _publish(
        video.owner_id,
        {
            "id": str(video.pk),
            "kind": kind,
            "status": video.status,
            "progress": video.progress if progress is None else progress,
            "error_message": video.error_message,
        },
    )


def publish_video_deleted(owner_id, video_id):
    _publish(owner_id, {"id": str(video_id), "kind": "deleted"})


def _sse(event, data="{}"):
    return f"event: {event}\ndata: {data}\n\n"


def event_stream(owner_id, expires_at):
    """Relay the owner's updates until the access token expires; the client then reconnects."""
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=5)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        pubsub.subscribe(channel_for(owner_id))
        # Sent after subscribing, so a refetch on "ready" cannot miss an update.
        yield "retry: 3000\n\n"
        yield _sse("ready")
        last_sent = time.monotonic()
        while time.time() < expires_at:
            message = pubsub.get_message(timeout=POLL_SECONDS)
            now = time.monotonic()
            if message:
                yield _sse("video", message["data"].decode())
                last_sent = now
            elif now - last_sent >= KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_sent = now
        yield _sse("expired")
    except RedisError:
        logger.warning("Live updates stream lost its connection to Redis")
    finally:
        pubsub.close()
        client.close()
