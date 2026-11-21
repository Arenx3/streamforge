from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import path, reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

User = get_user_model()
PASSWORD = "c0rrect-horse-battery"


class PrivateView(APIView):
    """Declares no permissions, so it shows what the project default enforces."""

    def get(self, request):
        return Response({"user": request.user.username})


urlpatterns = [path("private/", PrivateView.as_view())]


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AuthTestCase(APITestCase):
    register_url = reverse("auth-register")
    token_url = reverse("auth-token")
    refresh_url = reverse("auth-token-refresh")
    me_url = reverse("auth-me")

    def setUp(self):
        cache.clear()

    def make_user(self, username="alice", **extra):
        return User.objects.create_user(username=username, password=PASSWORD, **extra)

    def login(self, username="alice", password=PASSWORD, **extra):
        return self.client.post(
            self.token_url, {"username": username, "password": password}, format="json", **extra
        )

    def register(self, **overrides):
        payload = {"username": "newuser", "email": "new@example.com", "password": PASSWORD}
        return self.client.post(self.register_url, payload | overrides, format="json")


class RegisterTests(AuthTestCase):
    def test_creates_user_and_never_returns_password(self):
        response = self.register()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data), {"id", "username", "email", "date_joined"})
        user = User.objects.get(username="newuser")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)

    def test_email_is_optional(self):
        response = self.client.post(
            self.register_url, {"username": "noemail", "password": PASSWORD}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_rejects_passwords_that_fail_django_validators(self):
        weak_passwords = {
            "too short": "x7!kQ",
            "common": "password123",
            "numeric": "8675309214",
            "similar to username": "newuser1",
        }
        for reason, password in weak_passwords.items():
            with self.subTest(reason):
                response = self.register(password=password)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="newuser").exists())

    def test_rejects_username_taken_in_any_case(self):
        self.make_user("alice")

        for username in ("alice", "ALICE"):
            with self.subTest(username):
                response = self.register(username=username)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("username", response.data)

    def test_rejects_invalid_email(self):
        response = self.register(email="not-an-email")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_cannot_grant_itself_privileges(self):
        self.register(is_staff=True, is_superuser=True)

        user = User.objects.get(username="newuser")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_stale_authorization_header_is_ignored(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.invalid")

        response = self.register()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class LoginTests(AuthTestCase):
    def test_returns_access_and_refresh_tokens(self):
        user = self.make_user()

        response = self.login()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        access = AccessToken(response.data["access"])
        self.assertEqual(access["user_id"], str(user.id))
        self.assertEqual(access["exp"] - access["iat"], timedelta(minutes=15).total_seconds())
        self.assertIn("refresh", response.data)

    def test_wrong_password_is_rejected(self):
        self.make_user()

        response = self.login(password="wrong-password")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_is_rejected(self):
        self.make_user(is_active=False)

        response = self.login()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class RefreshTests(AuthTestCase):
    def refresh(self, token):
        return self.client.post(self.refresh_url, {"refresh": token}, format="json")

    def test_returns_new_access_and_rotated_refresh(self):
        self.make_user()
        original = self.login().data["refresh"]

        response = self.refresh(original)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertNotEqual(response.data["refresh"], original)

    def test_rotated_token_cannot_be_reused(self):
        self.make_user()
        original = self.login().data["refresh"]
        rotated = self.refresh(original).data["refresh"]

        self.assertEqual(self.refresh(original).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.refresh(rotated).status_code, status.HTTP_200_OK)

    def test_invalid_token_is_rejected(self):
        self.assertEqual(self.refresh("not-a-token").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_access_token_cannot_refresh(self):
        self.make_user()
        access = self.login().data["access"]

        self.assertEqual(self.refresh(access).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deactivated_user_cannot_refresh(self):
        user = self.make_user()
        token = self.login().data["refresh"]
        User.objects.filter(pk=user.pk).update(is_active=False)

        self.assertEqual(self.refresh(token).status_code, status.HTTP_401_UNAUTHORIZED)


class MeTests(AuthTestCase):
    def authenticate(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_returns_current_user(self):
        user = self.make_user(email="alice@example.com")
        self.authenticate(self.login().data["access"])

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["username"], "alice")
        self.assertEqual(response.data["email"], "alice@example.com")
        self.assertNotIn("password", response.data)

    def test_requires_token(self):
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_expired_token(self):
        token = AccessToken.for_user(self.make_user())
        token.set_exp(lifetime=-timedelta(minutes=1))
        self.authenticate(token)

        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_refresh_token_as_access(self):
        self.authenticate(RefreshToken.for_user(self.make_user()))

        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_token_of_deactivated_user(self):
        user = self.make_user()
        self.authenticate(AccessToken.for_user(user))
        User.objects.filter(pk=user.pk).update(is_active=False)

        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(ROOT_URLCONF="accounts.tests")
class DefaultPermissionTests(AuthTestCase):
    def test_new_api_views_are_private_by_default(self):
        self.assertEqual(self.client.get("/private/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_valid_token_passes_default_permission(self):
        token = AccessToken.for_user(self.make_user())
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get("/private/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"user": "alice"})


@mock.patch.dict(
    ScopedRateThrottle.THROTTLE_RATES, {"auth_login": "2/minute", "auth_register": "2/hour"}
)
class RateLimitTests(AuthTestCase):
    def login_from(self, forwarded_for):
        return self.login(password="wrong-password", HTTP_X_FORWARDED_FOR=forwarded_for)

    def test_login_is_limited_and_says_when_to_retry(self):
        codes = [self.login(password="wrong-password").status_code for _ in range(2)]
        blocked = self.login(password="wrong-password")

        self.assertEqual(codes, [status.HTTP_401_UNAUTHORIZED] * 2)
        self.assertEqual(blocked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", blocked.headers)

    def test_registration_is_limited(self):
        codes = [self.register(username=f"user{i}").status_code for i in range(3)]

        self.assertEqual(
            codes,
            [
                status.HTTP_201_CREATED,
                status.HTTP_201_CREATED,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ],
        )

    def test_spoofed_forwarded_for_does_not_bypass_limit(self):
        # nginx appends the real client IP, so only the last entry can be trusted.
        codes = [self.login_from(f"198.51.100.{i}, 203.0.113.9").status_code for i in range(3)]

        self.assertEqual(codes[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_limit_is_per_client(self):
        for _ in range(3):
            self.login_from("203.0.113.9")

        response = self.login_from("203.0.113.10")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
