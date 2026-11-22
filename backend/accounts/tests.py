import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError
from django.test import override_settings
from django.urls import path, reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from config.celery import app as celery_app

User = get_user_model()
EMAIL = "alice@example.com"
PASSWORD = "c0rrect-horse-battery"


class PrivateView(APIView):
    """Declares no permissions, so it shows what the project default enforces."""

    def get(self, request):
        return Response({"user": request.user.email})


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
        always_eager = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        self.addCleanup(setattr, celery_app.conf, "task_always_eager", always_eager)

    def make_user(self, email=EMAIL, **extra):
        return User.objects.create_user(email=email, password=PASSWORD, **extra)

    def login(self, email=EMAIL, password=PASSWORD, **extra):
        return self.client.post(
            self.token_url, {"email": email, "password": password}, format="json", **extra
        )

    def register(self, **overrides):
        payload = {"email": "new@example.com", "password": PASSWORD}
        return self.client.post(self.register_url, payload | overrides, format="json")


class RegisterTests(AuthTestCase):
    def test_creates_user_and_never_returns_password(self):
        response = self.register()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(set(response.data), {"detail"})
        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)

    def test_email_is_required(self):
        response = self.client.post(self.register_url, {"password": PASSWORD}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_rejects_passwords_that_fail_django_validators(self):
        weak_passwords = {
            "too short": "x7!kQ",
            "common": "password123",
            "numeric": "8675309214",
            "similar to email": "new@example.com",
        }
        for reason, password in weak_passwords.items():
            with self.subTest(reason):
                response = self.register(password=password)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_does_not_duplicate_an_email_in_any_form(self):
        self.make_user("kate@example.com")

        for email in ("kate@example.com", "KATE@Example.COM", "Kate@example.com"):
            with self.subTest(email):
                response = self.register(email=email)

                self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(User.objects.filter(email="kate@example.com").count(), 1)

    def test_stores_email_in_canonical_form(self):
        self.register(email="  New.User@Example.COM ")

        self.assertTrue(User.objects.filter(email="new.user@example.com").exists())

    def test_rejects_email_too_long_once_normalized(self):
        # "İ" passes the email validator but doubles in length when lowercased.
        response = self.register(email="İ" * 200 + "@example.com")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_rejects_invalid_email(self):
        response = self.register(email="not-an-email")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_cannot_grant_itself_privileges(self):
        self.register(is_staff=True, is_superuser=True)

        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_stale_authorization_header_is_ignored(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.invalid")

        response = self.register()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)


class LoginTests(AuthTestCase):
    def test_returns_access_and_refresh_tokens(self):
        user = self.make_user()

        response = self.login()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        access = AccessToken(response.data["access"])
        self.assertEqual(access["user_id"], str(user.id))
        self.assertEqual(access["exp"] - access["iat"], timedelta(minutes=15).total_seconds())
        self.assertIn("refresh", response.data)

    def test_email_is_case_insensitive(self):
        self.make_user()

        response = self.login(email="ALICE@Example.com")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

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
        user = self.make_user()
        self.authenticate(self.login().data["access"])

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["email"], EMAIL)
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


class UserModelTests(AuthTestCase):
    def test_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password=PASSWORD)

    def test_stores_canonical_email(self):
        user = User.objects.create_user(email="  Alice@EXAMPLE.com ", password=PASSWORD)

        self.assertEqual(user.email, EMAIL)

    def test_has_no_username(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertNotIn("username", [field.name for field in User._meta.get_fields()])

    def test_database_rejects_non_canonical_email(self):
        user = self.make_user()

        with self.assertRaises(IntegrityError):
            User.objects.filter(pk=user.pk).update(email="ALICE@example.com")

    def test_createsuperuser_command_uses_email(self):
        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": PASSWORD}):
            call_command("createsuperuser", "--noinput", "--email", "Root@Example.com", verbosity=0)

        user = User.objects.get(email="root@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))


class UserAdminTests(AuthTestCase):
    add_url = reverse("admin:accounts_user_add")

    def setUp(self):
        super().setUp()
        self.client.force_login(
            User.objects.create_superuser(email="root@example.com", password=PASSWORD)
        )

    def add_user(self, email):
        data = {
            "email": email,
            "usable_password": "true",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        return self.client.post(self.add_url, data)

    def test_pages_load(self):
        user = self.make_user()
        urls = [
            self.add_url,
            reverse("admin:accounts_user_changelist") + "?q=alice",
            reverse("admin:accounts_user_change", args=[user.pk]),
        ]
        for url in urls:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_add_user_stores_canonical_email(self):
        response = self.add_user("New@Example.com")

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

    def test_add_user_rejects_email_taken_in_any_case(self):
        self.make_user()

        response = self.add_user("ALICE@example.com")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.filter(email__iexact=EMAIL).count(), 1)

    def test_login_with_email_in_any_case(self):
        self.client.logout()

        response = self.client.post(
            reverse("admin:login"), {"username": "ROOT@example.com", "password": PASSWORD}
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)


@override_settings(ROOT_URLCONF="accounts.tests")
class DefaultPermissionTests(AuthTestCase):
    def test_new_api_views_are_private_by_default(self):
        self.assertEqual(self.client.get("/private/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_valid_token_passes_default_permission(self):
        token = AccessToken.for_user(self.make_user())
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get("/private/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"user": EMAIL})


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
        codes = [self.register(email=f"user{i}@example.com").status_code for i in range(3)]

        self.assertEqual(
            codes,
            [
                status.HTTP_202_ACCEPTED,
                status.HTTP_202_ACCEPTED,
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
