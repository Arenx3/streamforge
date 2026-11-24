"""Helpers for tests that hold a session the way a browser does."""

from accounts.session import REFRESH_COOKIE


def session_of(response):
    """The access token from the body and the refresh token from the cookie."""
    return {"access": response.data["access"], "refresh": response.cookies[REFRESH_COOKIE].value}


def post_with_refresh(client, url, token):
    client.cookies[REFRESH_COOKIE] = token
    return client.post(url)
