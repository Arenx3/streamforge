"""The refresh token travels in an httpOnly cookie, out of reach of the page's scripts."""

from django.conf import settings

REFRESH_COOKIE = "streamforge_refresh"
# Only the endpoints that renew or end the session receive it.
REFRESH_COOKIE_PATH = "/api/auth/"


def keep_refresh_in_cookie(response):
    """Move the refresh token of a successful response from its body into the cookie."""
    token = response.data.pop("refresh", None) if isinstance(response.data, dict) else None
    if token:
        response.set_cookie(
            REFRESH_COOKIE,
            token,
            max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
            path=REFRESH_COOKIE_PATH,
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="Strict",
        )
    return response


def forget_refresh_cookie(response):
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, samesite="Strict")
    return response


def refresh_from_cookie(request):
    return request.COOKIES.get(REFRESH_COOKIE, "")
