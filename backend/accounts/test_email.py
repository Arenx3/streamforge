import importlib
import re
import time
from datetime import timedelta
from smtplib import SMTPServerDisconnected
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.tasks import delete_unverified_accounts, send_verification_email
from accounts.tests import EMAIL, PASSWORD, AuthTestCase
from accounts.tokens import RESET_PASSWORD, VERIFY_EMAIL, make_token, read_token
from videos.models import Video

User = get_user_model()
NEW_PASSWORD = "an0ther-long-passphrase"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def link_token(message):
    return re.search(r"#token=([\w.\-]+)", message.body).group(1)


@override_settings(CACHES=LOCMEM, FRONTEND_URL="https://app.example.com")
class EmailTestCase(AuthTestCase):
    verify_url = reverse("auth-email-verify")
    resend_url = reverse("auth-email-resend")
    reset_url = reverse("auth-password-reset")
    confirm_url = reverse("auth-password-reset-confirm")

    def setUp(self):
        super().setUp()
        cache.clear()

    def verified_user(self, email=EMAIL):
        return self.make_user(email, email_verified_at=timezone.now())

    def verify(self, token):
        return self.client.post(self.verify_url, {"token": token}, format="json")

    def request_reset(self, email=EMAIL):
        return self.client.post(self.reset_url, {"email": email}, format="json")

    def confirm_reset(self, token, password=NEW_PASSWORD):
        return self.client.post(
            self.confirm_url, {"token": token, "password": password}, format="json"
        )


class RegistrationEmailTests(EmailTestCase):
    def test_new_account_starts_unverified_and_gets_a_link(self):
        self.register()

        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertEqual(read_token(VERIFY_EMAIL, link_token(mail.outbox[0])), user)

    def test_link_uses_the_frontend_and_keeps_the_token_in_the_fragment(self):
        self.register()

        message = mail.outbox[0]
        self.assertIn("https://app.example.com/verify-email#token=", message.body)
        html = message.alternatives[0].content
        self.assertIn('href="https://app.example.com/verify-email#token=', html)

    @override_settings(ALLOWED_HOSTS=["evil.example"])
    def test_links_ignore_a_forged_host_header(self):
        self.client.post(
            self.register_url,
            {"email": "new@example.com", "password": PASSWORD},
            format="json",
            HTTP_HOST="evil.example",
            HTTP_X_FORWARDED_HOST="evil.example",
        )

        self.assertNotIn("evil.example", mail.outbox[0].body)

    def test_response_never_reveals_whether_the_email_exists(self):
        self.make_user("taken@example.com")

        taken = self.register(email="taken@example.com")
        fresh = self.register(email="fresh@example.com")

        self.assertEqual(taken.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(fresh.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(taken.data, fresh.data)

    def test_taken_email_keeps_its_password_and_gets_a_notice(self):
        user = self.verified_user()

        self.register(email=EMAIL.upper(), password=NEW_PASSWORD)

        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(User.objects.filter(email=EMAIL).count(), 1)
        self.assertEqual(mail.outbox[0].subject, "Ya tienes una cuenta en StreamForge")
        self.assertNotIn("#token=", mail.outbox[0].body)

    def test_notices_to_one_address_are_limited(self):
        self.make_user()

        for _ in range(3):
            self.register(email=EMAIL)

        self.assertEqual(len(mail.outbox), 1)

    def test_broker_failure_still_answers_the_same(self):
        with (
            mock.patch(
                "accounts.services.send_verification_email.delay",
                side_effect=OperationalError("down"),
            ),
            self.assertLogs("accounts.services", level="ERROR"),
        ):
            response = self.register()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())


class VerifyEmailTests(EmailTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.token = make_token(VERIFY_EMAIL, self.user)
        self.client.force_authenticate(self.user)

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", response.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_valid_link_verifies_the_account(self):
        response = self.verify(self.token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["email_verified"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_me_reports_the_state(self):
        self.assertFalse(self.client.get(self.me_url).data["email_verified"])

        self.verify(self.token)

        self.assertTrue(self.client.get(self.me_url).data["email_verified"])

    def test_opening_the_link_twice_is_fine(self):
        self.verify(self.token)
        first = User.objects.get(pk=self.user.pk).email_verified_at

        response = self.verify(self.token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.get(pk=self.user.pk).email_verified_at, first)

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.verify(self.token).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_link_of_another_account_is_rejected(self):
        other = self.make_user("other@example.com")

        self.assert_rejected(self.verify(make_token(VERIFY_EMAIL, other)))

    def test_rejects_tampered_tokens(self):
        for token in (self.token[:-1] + ("a" if self.token[-1] != "a" else "b"), "garbage"):
            with self.subTest(token):
                self.assert_rejected(self.verify(token))

    def test_rejects_expired_links(self):
        expired = time.time() + 86400 + 1

        with mock.patch("django.core.signing.time.time", return_value=expired):
            self.assert_rejected(self.verify(self.token))

    def test_reset_token_cannot_verify(self):
        self.assert_rejected(self.verify(make_token(RESET_PASSWORD, self.user)))

    def test_link_dies_if_the_email_changes(self):
        User.objects.filter(pk=self.user.pk).update(email="changed@example.com")
        self.user.refresh_from_db()

        self.assert_rejected(self.verify(self.token))


class ResendVerificationTests(EmailTestCase):
    def test_sends_a_new_link(self):
        user = self.make_user()
        self.client.force_authenticate(user)

        response = self.client.post(self.resend_url)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(read_token(VERIFY_EMAIL, link_token(mail.outbox[0])), user)

    def test_repeated_requests_are_limited(self):
        self.client.force_authenticate(self.make_user())

        for _ in range(3):
            self.client.post(self.resend_url)

        self.assertEqual(len(mail.outbox), 1)

    def test_verified_account_gets_nothing(self):
        self.client.force_authenticate(self.verified_user())

        self.client.post(self.resend_url)

        self.assertEqual(mail.outbox, [])

    def test_requires_login(self):
        self.assertEqual(
            self.client.post(self.resend_url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_broker_failure_is_a_503(self):
        self.client.force_authenticate(self.make_user())

        with (
            mock.patch(
                "accounts.services.send_verification_email.delay",
                side_effect=OperationalError("down"),
            ),
            self.assertLogs("accounts.services", level="ERROR"),
        ):
            response = self.client.post(self.resend_url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class PasswordResetTests(EmailTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.verified_user()

    def reset_token(self):
        self.request_reset()
        return link_token(mail.outbox[-1])

    def test_sends_a_link_with_any_casing(self):
        response = self.request_reset(EMAIL.upper())

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertIn("https://app.example.com/reset-password#token=", mail.outbox[0].body)

    def test_unknown_email_gets_the_same_answer_and_no_mail(self):
        known = self.request_reset()
        unknown = self.request_reset("nobody@example.com")

        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_inactive_account_gets_no_mail(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertEqual(self.request_reset().status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(mail.outbox, [])

    def test_requests_for_one_address_are_limited(self):
        for _ in range(3):
            self.request_reset()

        self.assertEqual(len(mail.outbox), 1)

    def test_broker_failure_is_a_503(self):
        with (
            mock.patch(
                "accounts.services.send_password_reset_email.delay",
                side_effect=OperationalError("down"),
            ),
            self.assertLogs("accounts.services", level="ERROR"),
        ):
            response = self.request_reset()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_changes_the_password(self):
        response = self.confirm_reset(self.reset_token())

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.login(password=PASSWORD).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code, status.HTTP_200_OK)

    def test_link_works_only_once(self):
        token = self.reset_token()
        self.confirm_reset(token)

        response = self.confirm_reset(token, "yet-another-passphrase-9")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_rejects_expired_links(self):
        token = self.reset_token()
        expired = time.time() + 3600 + 1

        with mock.patch("django.core.signing.time.time", return_value=expired):
            response = self.confirm_reset(token)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", response.data)

    def test_verification_token_cannot_reset(self):
        response = self.confirm_reset(make_token(VERIFY_EMAIL, self.user))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_account_cannot_reset(self):
        token = self.reset_token()
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertEqual(self.confirm_reset(token).status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_weak_passwords_and_keeps_the_link(self):
        token = self.reset_token()

        for password in ("short", "password123", EMAIL):
            with self.subTest(password):
                response = self.confirm_reset(token, password)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("password", response.data)
        self.assertEqual(self.confirm_reset(token).status_code, status.HTTP_204_NO_CONTENT)

    def test_closes_every_session(self):
        session = self.login().data
        rotated = self.client.post(self.refresh_url, {"refresh": session["refresh"]}).data

        self.confirm_reset(self.reset_token())

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rotated['access']}")
        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.credentials()
        for refresh in (session["refresh"], rotated["refresh"]):
            response = self.client.post(self.refresh_url, {"refresh": refresh})

            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_new_session_works_after_the_reset(self):
        self.confirm_reset(self.reset_token())

        access = self.login(password=NEW_PASSWORD).data["access"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_200_OK)

    def test_reset_proves_the_inbox(self):
        unverified = self.make_user("pending@example.com")

        response = self.confirm_reset(make_token(RESET_PASSWORD, unverified))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        unverified.refresh_from_db()
        self.assertTrue(unverified.email_verified)


class AccountPreHijackingTests(EmailTestCase):
    """Someone registers a victim's email first and waits for the victim to confirm it."""

    def setUp(self):
        super().setUp()
        self.register(email="victim@example.com", password=PASSWORD)
        self.victim_link = link_token(mail.outbox[0])
        self.attacker = self.login("victim@example.com").data
        self.account = User.objects.get(email="victim@example.com")

    def test_victim_cannot_confirm_without_the_attackers_password(self):
        self.assertEqual(self.verify(self.victim_link).status_code, status.HTTP_401_UNAUTHORIZED)
        self.account.refresh_from_db()
        self.assertFalse(self.account.email_verified)

    def test_registering_again_does_not_take_over_or_replace_the_password(self):
        self.register(email="victim@example.com", password=NEW_PASSWORD)

        self.account.refresh_from_db()
        self.assertFalse(self.account.email_verified)
        self.assertTrue(self.account.check_password(PASSWORD))

    def test_victim_recovers_it_and_locks_the_attacker_out(self):
        self.request_reset("victim@example.com")
        self.confirm_reset(link_token(mail.outbox[-1]))

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.attacker['access']}")
        self.assertEqual(self.client.get(self.me_url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.credentials()
        response = self.client.post(self.refresh_url, {"refresh": self.attacker["refresh"]})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.login("victim@example.com", PASSWORD).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.account.refresh_from_db()
        self.assertTrue(self.account.email_verified)


class EmailDeliveryTests(EmailTestCase):
    def test_retries_when_smtp_fails(self):
        user = self.make_user()
        send = "accounts.mail.EmailMultiAlternatives.send"

        with mock.patch(send, side_effect=[SMTPServerDisconnected("down"), 1]) as fake_send:
            send_verification_email.apply(args=[user.pk])

        self.assertEqual(fake_send.call_count, 2)

    def test_verified_account_is_skipped(self):
        send_verification_email.apply(args=[self.verified_user().pk])

        self.assertEqual(mail.outbox, [])

    def test_has_text_and_html(self):
        self.register()

        message = mail.outbox[0]
        self.assertEqual(message.alternatives[0].mimetype, "text/html")
        self.assertIn("Confirma tu correo", message.body)
        self.assertNotIn("<", message.body)


@override_settings(ACCOUNT_UNVERIFIED_TTL_HOURS=72)
class DeleteUnverifiedAccountsTests(EmailTestCase):
    def old_user(self, email="old@example.com", **extra):
        user = self.make_user(email, **extra)
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(hours=73))
        return user

    def test_deletes_old_unverified_accounts_and_frees_the_email(self):
        self.old_user()

        self.assertEqual(delete_unverified_accounts(), {"deleted": 1})
        self.assertEqual(self.register(email="old@example.com").status_code, 202)
        self.assertTrue(User.objects.filter(email="old@example.com").exists())

    def test_keeps_recent_accounts(self):
        self.make_user("recent@example.com")

        self.assertEqual(delete_unverified_accounts(), {"deleted": 0})

    def test_keeps_verified_accounts(self):
        self.old_user(email_verified_at=timezone.now())

        self.assertEqual(delete_unverified_accounts(), {"deleted": 0})

    def test_never_deletes_staff(self):
        self.old_user("staff@example.com", is_staff=True)
        self.old_user("root@example.com", is_superuser=True)

        self.assertEqual(delete_unverified_accounts(), {"deleted": 0})

    def test_never_deletes_an_account_with_videos(self):
        user = self.old_user()
        Video.objects.create(owner=user, title="Keep me")

        self.assertEqual(delete_unverified_accounts(), {"deleted": 0})
        self.assertTrue(Video.objects.filter(owner=user).exists())

    def test_outstanding_tokens_do_not_block_the_cleanup(self):
        RefreshToken.for_user(self.old_user())

        self.assertEqual(delete_unverified_accounts(), {"deleted": 1})


class AccountCreationTests(EmailTestCase):
    def test_superuser_is_verified(self):
        user = User.objects.create_superuser(email="root@example.com", password=PASSWORD)

        self.assertTrue(user.email_verified)

    def test_user_created_in_admin_is_verified(self):
        admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD)
        self.client.force_login(admin)

        self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "email": "staffmade@example.com",
                "usable_password": "true",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )

        self.assertTrue(User.objects.get(email="staffmade@example.com").email_verified)

    def test_migration_marks_existing_accounts_verified(self):
        user = self.make_user()
        migration = importlib.import_module("accounts.migrations.0002_user_email_verified_at")

        migration.mark_existing_accounts_verified(apps, None)

        user.refresh_from_db()
        self.assertEqual(user.email_verified_at, user.date_joined)


@mock.patch.dict(
    ScopedRateThrottle.THROTTLE_RATES,
    {
        "auth_password_reset": "2/hour",
        "auth_password_confirm": "2/hour",
        "auth_email_verify": "2/hour",
        "auth_email_resend": "2/hour",
    },
)
class EmailRateLimitTests(EmailTestCase):
    def assert_third_is_blocked(self, call):
        codes = [call().status_code for _ in range(3)]

        self.assertNotEqual(codes[1], status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_password_reset_requests(self):
        self.assert_third_is_blocked(self.request_reset)

    def test_password_reset_confirmations(self):
        self.assert_third_is_blocked(lambda: self.confirm_reset("garbage"))

    def test_email_verification(self):
        self.client.force_authenticate(self.make_user())

        self.assert_third_is_blocked(lambda: self.verify("garbage"))

    def test_resend(self):
        self.client.force_authenticate(self.make_user())

        self.assert_third_is_blocked(lambda: self.client.post(self.resend_url))
