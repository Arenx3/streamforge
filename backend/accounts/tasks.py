from datetime import timedelta
from functools import partial
from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from redis.exceptions import RedisError

from accounts import mail

User = get_user_model()

email_task = partial(
    shared_task,
    bind=True,
    ignore_result=True,
    autoretry_for=(SMTPException, OSError, RedisError),
    retry_backoff=15,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)


def _may_send(task, kind, user):
    """Caps mail per address, so nobody can flood a victim's inbox from many IPs."""
    if task.request.retries:
        return True
    return cache.add(f"auth-email:{kind}:{user.pk}", 1, settings.AUTH_EMAIL_COOLDOWN_SECONDS)


@email_task(name="accounts.send_verification_email")
def send_verification_email(self, user_id):
    user = User.objects.filter(pk=user_id, is_active=True, email_verified_at__isnull=True).first()
    if user and _may_send(self, "verify", user):
        mail.send_verification(user)


@email_task(name="accounts.send_account_exists_email")
def send_account_exists_email(self, email):
    user = User.objects.filter(email=email, is_active=True).first()
    if user and _may_send(self, "exists", user):
        mail.send_account_exists(user)


@email_task(name="accounts.send_password_reset_email")
def send_password_reset_email(self, email):
    user = User.objects.filter(email=email, is_active=True).first()
    if user and _may_send(self, "reset", user):
        mail.send_password_reset(user)


@shared_task(name="accounts.delete_unverified_accounts")
def delete_unverified_accounts():
    """Free emails claimed by someone who never proved they own them."""
    cutoff = timezone.now() - timedelta(hours=settings.ACCOUNT_UNVERIFIED_TTL_HOURS)
    stale = User.objects.filter(
        email_verified_at__isnull=True,
        date_joined__lt=cutoff,
        is_staff=False,
        is_superuser=False,
        videos__isnull=True,
    )
    _, deleted = stale.delete()
    return {"deleted": deleted.get("accounts.User", 0)}
