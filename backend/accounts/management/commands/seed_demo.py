import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.demo import ensure_demo_video, get_demo_user

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create the local demo account and queue its sample video."

    def handle(self, *args, **options):
        if not settings.DEMO_ENABLED:
            return
        try:
            ensure_demo_video(get_demo_user())
        except Exception:
            # The demo is optional: it must never keep the API from starting.
            logger.exception("Could not prepare the demo account")
