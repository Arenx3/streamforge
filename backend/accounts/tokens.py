"""Signed, expiring links for email verification and password reset."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils.crypto import constant_time_compare, salted_hmac

VERIFY_EMAIL = "verify-email"
RESET_PASSWORD = "reset-password"


def _signer(purpose):
    return signing.TimestampSigner(salt=f"accounts.{purpose}", sep=".")


def _max_age(purpose):
    if purpose == VERIFY_EMAIL:
        return settings.EMAIL_VERIFICATION_TTL_SECONDS
    return settings.PASSWORD_RESET_TTL_SECONDS


def _fingerprint(purpose, user):
    """Changes when the link must stop working: a reset changes the password hash."""
    state = f"{user.pk}:{user.email}"
    if purpose == RESET_PASSWORD:
        state += f":{user.password}"
    return salted_hmac(f"accounts.{purpose}", state, algorithm="sha256").hexdigest()[:32]


def make_token(purpose, user):
    return _signer(purpose).sign_object({"u": str(user.pk), "f": _fingerprint(purpose, user)})


def read_token(purpose, token, users=None):
    """Return the active user the token belongs to, or None if it is not valid."""
    try:
        data = _signer(purpose).unsign_object(token, max_age=_max_age(purpose))
    except signing.BadSignature:
        return None
    users = get_user_model().objects.all() if users is None else users
    user = users.filter(pk=data["u"], is_active=True).first()
    if user is None or not constant_time_compare(data["f"], _fingerprint(purpose, user)):
        return None
    return user
