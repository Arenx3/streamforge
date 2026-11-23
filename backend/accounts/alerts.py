"""Operational alerts about email delivery. Messages never include personal data."""

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_SECONDS = 900
WEBHOOK_TIMEOUT_SECONDS = 5


def alert(summary):
    """Log at CRITICAL and notify the optional webhook, without flooding it in an outage."""
    logger.critical(summary)
    url = settings.EMAIL_ALERT_WEBHOOK_URL
    if not url:
        return
    if urlsplit(url).scheme not in {"http", "https"}:
        logger.warning("EMAIL_ALERT_WEBHOOK_URL must be an http(s) URL")
        return
    try:
        if not cache.add("email-alert", 1, ALERT_COOLDOWN_SECONDS):
            return
    except RedisError:
        pass  # An alert matters most while the infrastructure is failing.
    message = f"StreamForge: {summary}"
    request = urllib.request.Request(
        url,
        # "text" is Slack's field and "content" is Discord's.
        data=json.dumps({"text": message, "content": message}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "StreamForge/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS):
            pass
    except (OSError, ValueError):
        logger.warning("Could not deliver the email alert to the webhook")
