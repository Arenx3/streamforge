import contextlib
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.tasks import (
    send_account_exists_email,
    send_password_reset_email,
    send_verification_email,
)
from accounts.tokens import RESET_PASSWORD, VERIFY_EMAIL, read_token

logger = logging.getLogger(__name__)
User = get_user_model()
INVALID_LINK = "This link is invalid or has expired."


class EmailUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The email could not be sent. Try again."
    default_code = "email_unavailable"


def _enqueue(task, *args):
    try:
        task.delay(*args)
    except BrokerError as exc:
        logger.exception("Could not queue %s", task.name)
        raise EmailUnavailable from exc


def register_account(email, password):
    """Does the same work whether or not the email is taken, so it cannot be probed."""
    try:
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password)
    except IntegrityError:
        task, argument = send_account_exists_email, email
    else:
        task, argument = send_verification_email, user.pk
    # The account exists either way: its owner can ask for the email again after logging in.
    with contextlib.suppress(EmailUnavailable):
        _enqueue(task, argument)


def resend_verification(user):
    if not user.email_verified:
        _enqueue(send_verification_email, user.pk)


def confirm_email(user, token):
    """Needs the login and the link: password alone or inbox alone cannot verify."""
    if read_token(VERIFY_EMAIL, token) != user:
        raise serializers.ValidationError({"token": [INVALID_LINK]})
    if not user.email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])


def request_password_reset(email):
    # The worker looks the account up, so this path is the same for unknown emails.
    _enqueue(send_password_reset_email, email)


@transaction.atomic
def reset_password(token, password):
    user = read_token(RESET_PASSWORD, token, users=User.objects.select_for_update())
    if user is None:
        raise serializers.ValidationError({"token": [INVALID_LINK]})
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": exc.messages}) from exc
    user.set_password(password)
    # The link reached this inbox, which proves the owner controls it.
    user.email_verified_at = user.email_verified_at or timezone.now()
    user.save(update_fields=["password", "email_verified_at"])
    active = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in active], ignore_conflicts=True
    )
