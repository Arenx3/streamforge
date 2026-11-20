import logging

import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .storage import s3_client

logger = logging.getLogger(__name__)


def _check(name, fn):
    try:
        fn()
        return True
    except Exception:
        logger.exception("Health check failed: %s", name)
        return False


@require_GET
def health(request):
    checks = {
        "database": _check("database", connection.ensure_connection),
        "redis": _check(
            "redis",
            lambda: redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2).ping(),
        ),
        "storage": _check(
            "storage",
            lambda: s3_client().head_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME),
        ),
    }
    healthy = all(checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )
