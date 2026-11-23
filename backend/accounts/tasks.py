from datetime import timedelta
from functools import partial
from smtplib import SMTPException

from celery import Task, shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from redis.exceptions import RedisError

from accounts import mail
from accounts.alerts import alert

User = get_user_model()

RECOVERY_KINDS = {"reset", "changed"}
SIGNUP_SHARE = 0.8
DAILY_COUNTER_TTL_SECONDS = 2 * 86400


class EmailTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Only the error type: SMTP errors can quote the recipient's address.
        alert(f"{self.name} gave up after its retries: {type(exc).__name__}")


email_task = partial(
    shared_task,
    base=EmailTask,
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
    cooldown_key = f"auth-email:{kind}:{user.pk}"
    if not cache.add(cooldown_key, 1, settings.AUTH_EMAIL_COOLDOWN_SECONDS):
        return False
    if _reserve_daily_slot(kind):
        return True
    cache.delete(cooldown_key)  # So the person can ask again once there is quota.
    return False


def _reserve_daily_slot(kind):
    """Anyone can trigger signup mail, so it never gets the share kept for account recovery."""
    limit = settings.AUTH_EMAIL_DAILY_LIMIT
    if limit <= 0:
        return True
    cap = limit if kind in RECOVERY_KINDS else int(limit * SIGNUP_SHARE)
    key = f"auth-email-daily:{timezone.now():%Y-%m-%d}"
    cache.add(key, 0, DAILY_COUNTER_TTL_SECONDS)
    if cache.incr(key) <= cap:
        return True
    cache.decr(key)
    alert(f"Daily email limit reached: {kind} emails are paused until tomorrow.")
    return False


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


@email_task(name="accounts.send_password_changed_email")
def send_password_changed_email(self, user_id):
    user = User.objects.filter(pk=user_id, is_active=True).first()
    # No per-address cooldown: every password change must be reported.
    if user and (self.request.retries or _reserve_daily_slot("changed")):
        mail.send_password_changed(user)


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
