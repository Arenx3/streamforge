"""The local demo account: one click in, with a processed sample video."""

import contextlib

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from videos.tasks import create_demo_video

DEMO_EMAIL = "demo@streamforge.local"


def get_demo_user():
    """The account has no usable password: the only way in is the demo endpoint."""
    users = get_user_model().objects
    user, _ = users.get_or_create(
        email=DEMO_EMAIL,
        defaults={"password": make_password(None), "email_verified_at": timezone.now()},
    )
    if user.has_usable_password() or not user.email_verified:
        user.set_unusable_password()
        user.email_verified_at = user.email_verified_at or timezone.now()
        user.save(update_fields=["password", "email_verified_at"])
    return user


def ensure_demo_video(user):
    if not user.videos.exists():
        with contextlib.suppress(BrokerError):
            create_demo_video.delay(user.pk)
