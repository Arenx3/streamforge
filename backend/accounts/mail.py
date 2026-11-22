"""Transactional account emails. Links only ever use FRONTEND_URL, never a request's host."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from accounts.tokens import RESET_PASSWORD, VERIFY_EMAIL, make_token


def _hours(seconds):
    hours = max(round(seconds / 3600), 1)
    return "1 hora" if hours == 1 else f"{hours} horas"


def _link(path, token=None):
    url = f"{settings.FRONTEND_URL.rstrip('/')}/{path}"
    # In the fragment, the token never reaches server logs or Referer headers.
    return f"{url}#token={token}" if token else url


def _send(user, **content):
    text = render_to_string("accounts/email/message.txt", content)
    html = render_to_string("accounts/email/message.html", content)
    message = EmailMultiAlternatives(content["subject"], text, to=[user.email])
    message.attach_alternative(html, "text/html")
    message.send()


def send_verification(user):
    days = round(settings.ACCOUNT_UNVERIFIED_TTL_HOURS / 24)
    _send(
        user,
        subject="Confirma tu correo en StreamForge",
        heading="Confirma tu correo",
        body=(
            "Confirma que este correo es tuyo para empezar a subir videos. "
            f"El enlace caduca en {_hours(settings.EMAIL_VERIFICATION_TTL_SECONDS)}."
        ),
        action="Confirmar correo",
        url=_link("verify-email", make_token(VERIFY_EMAIL, user)),
        note=(
            "Si no creaste esta cuenta, ignora este mensaje. "
            f"Se eliminará sola en {days} {'día' if days == 1 else 'días'}."
        ),
    )


def send_password_reset(user):
    _send(
        user,
        subject="Restablece tu contraseña de StreamForge",
        heading="Restablece tu contraseña",
        body=(
            "Recibimos una solicitud para cambiar tu contraseña. El enlace funciona "
            f"una sola vez y caduca en {_hours(settings.PASSWORD_RESET_TTL_SECONDS)}."
        ),
        action="Elegir contraseña nueva",
        url=_link("reset-password", make_token(RESET_PASSWORD, user)),
        note="Si no la pediste, ignora este mensaje. Tu contraseña no cambiará.",
    )


def send_account_exists(user):
    _send(
        user,
        subject="Ya tienes una cuenta en StreamForge",
        heading="Ya tienes una cuenta",
        body=(
            "Alguien intentó registrarse con este correo, que ya tiene una cuenta. "
            "Si fuiste tú, inicia sesión o restablece tu contraseña."
        ),
        action="Restablecer contraseña",
        url=_link("forgot-password"),
        note="Si no fuiste tú, no necesitas hacer nada.",
    )
