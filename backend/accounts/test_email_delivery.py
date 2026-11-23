import json
from datetime import timedelta
from smtplib import SMTPRecipientsRefused
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status

from accounts.alerts import alert
from accounts.tasks import (
    send_account_exists_email,
    send_password_changed_email,
    send_password_reset_email,
    send_verification_email,
)
from accounts.test_email import LOCMEM, NEW_PASSWORD, EmailTestCase
from accounts.tests import EMAIL
from accounts.tokens import RESET_PASSWORD, make_token

User = get_user_model()
WEBHOOK = "https://hooks.example.com/alert"


class PasswordChangedNoticeTests(EmailTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.verified_user()

    def test_reset_notifies_the_owner(self):
        response = self.confirm_reset(make_token(RESET_PASSWORD, self.user))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertEqual(mail.outbox[0].subject, "Tu contraseña de StreamForge cambió")

    def test_notice_carries_no_token(self):
        self.confirm_reset(make_token(RESET_PASSWORD, self.user))

        self.assertNotIn("#token=", mail.outbox[0].body)
        self.assertIn("https://app.example.com/forgot-password", mail.outbox[0].body)

    def test_failed_reset_sends_nothing(self):
        self.confirm_reset("garbage")

        self.assertEqual(mail.outbox, [])

    def test_every_change_is_reported(self):
        self.confirm_reset(make_token(RESET_PASSWORD, self.user))
        self.user.refresh_from_db()

        self.confirm_reset(make_token(RESET_PASSWORD, self.user), "yet-another-passphrase-9")

        self.assertEqual(len(mail.outbox), 2)

    def test_broker_failure_does_not_undo_the_change(self):
        with (
            mock.patch(
                "accounts.services.send_password_changed_email.delay",
                side_effect=OperationalError("down"),
            ),
            self.assertLogs("accounts.services", level="ERROR"),
        ):
            response = self.confirm_reset(make_token(RESET_PASSWORD, self.user))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_inactive_account_gets_no_notice(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        send_password_changed_email.apply(args=[self.user.pk])

        self.assertEqual(mail.outbox, [])


@override_settings(AUTH_EMAIL_DAILY_LIMIT=5)
class DailyLimitTests(EmailTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("accounts.tasks.alert")
        self.alert = patcher.start()
        self.addCleanup(patcher.stop)

    def users(self, count, prefix="user"):
        return [self.make_user(f"{prefix}{index}@example.com") for index in range(count)]

    def flood_signups(self, count):
        for user in self.users(count):
            send_verification_email.apply(args=[user.pk])

    def test_signup_mail_stops_at_its_share(self):
        self.flood_signups(10)

        self.assertEqual(len(mail.outbox), 4)
        self.alert.assert_called()

    def test_account_notices_share_the_signup_cap(self):
        self.flood_signups(4)
        user = self.make_user("taken@example.com")

        send_account_exists_email.apply(args=[user.email])

        self.assertEqual(len(mail.outbox), 4)

    def test_a_signup_flood_cannot_block_password_resets(self):
        self.flood_signups(50)
        user = self.verified_user()

        send_password_reset_email.apply(args=[user.email])

        self.assertEqual(len(mail.outbox), 5)
        self.assertEqual(mail.outbox[-1].to, [user.email])

    def test_nothing_goes_out_past_the_full_limit(self):
        for index, user in enumerate(self.users(8)):
            with self.subTest(index):
                send_password_reset_email.apply(args=[user.email])

        self.assertEqual(len(mail.outbox), 5)

    def test_password_change_notices_use_the_reserved_share(self):
        self.flood_signups(10)
        user = self.verified_user()

        send_password_changed_email.apply(args=[user.pk])

        self.assertEqual(mail.outbox[-1].subject, "Tu contraseña de StreamForge cambió")

    def test_refused_mail_can_be_requested_again_tomorrow(self):
        self.flood_signups(4)
        user = self.make_user("late@example.com")
        send_verification_email.apply(args=[user.pk])
        self.assertEqual(len(mail.outbox), 4)

        tomorrow = timezone.now() + timedelta(days=1)
        with mock.patch("accounts.tasks.timezone.now", return_value=tomorrow):
            send_verification_email.apply(args=[user.pk])

        self.assertEqual(mail.outbox[-1].to, ["late@example.com"])

    def test_cooldown_skips_do_not_spend_quota(self):
        user = self.make_user()

        for _ in range(10):
            send_verification_email.apply(args=[user.pk])
        self.flood_signups(3)

        self.assertEqual(len(mail.outbox), 4)

    @override_settings(AUTH_EMAIL_DAILY_LIMIT=0)
    def test_zero_means_no_limit(self):
        self.flood_signups(8)

        self.assertEqual(len(mail.outbox), 8)
        self.alert.assert_not_called()


class DeliveryFailureAlertTests(EmailTestCase):
    def test_alerts_when_retries_run_out_without_personal_data(self):
        user = self.make_user()
        refused = SMTPRecipientsRefused({user.email: (550, b"rejected")})

        with (
            mock.patch("accounts.mail.EmailMultiAlternatives.send", side_effect=refused),
            mock.patch("accounts.tasks.alert") as fake_alert,
        ):
            send_verification_email.apply(args=[user.pk])

        fake_alert.assert_called()
        summary = fake_alert.call_args.args[0]
        self.assertIn("accounts.send_verification_email", summary)
        self.assertIn("SMTPRecipientsRefused", summary)
        self.assertNotIn(user.email, summary)

    def test_a_delivered_email_raises_no_alert(self):
        user = self.make_user()

        with mock.patch("accounts.tasks.alert") as fake_alert:
            send_verification_email.apply(args=[user.pk])

        fake_alert.assert_not_called()


@override_settings(CACHES=LOCMEM, EMAIL_ALERT_WEBHOOK_URL=WEBHOOK)
class AlertTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch("accounts.alerts.urllib.request.urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def test_logs_and_calls_the_webhook(self):
        with self.assertLogs("accounts.alerts", level="CRITICAL"):
            alert("Delivery failed")

        request = self.urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, WEBHOOK)
        self.assertEqual(body["text"], "StreamForge: Delivery failed")
        self.assertEqual(body["content"], body["text"])
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 5)

    def test_sets_a_user_agent_webhooks_accept(self):
        with self.assertLogs("accounts.alerts", level="CRITICAL"):
            alert("Delivery failed")

        self.assertEqual(self.urlopen.call_args.args[0].get_header("User-agent"), "StreamForge/1.0")

    def test_an_outage_sends_one_webhook(self):
        with self.assertLogs("accounts.alerts", level="CRITICAL") as logs:
            for _ in range(5):
                alert("Delivery failed")

        self.urlopen.assert_called_once()
        self.assertEqual(len(logs.output), 5)

    @override_settings(EMAIL_ALERT_WEBHOOK_URL="")
    def test_webhook_is_optional(self):
        with self.assertLogs("accounts.alerts", level="CRITICAL"):
            alert("Delivery failed")

        self.urlopen.assert_not_called()

    @override_settings(EMAIL_ALERT_WEBHOOK_URL="file:///etc/passwd")
    def test_rejects_urls_that_are_not_http(self):
        with self.assertLogs("accounts.alerts", level="WARNING"):
            alert("Delivery failed")

        self.urlopen.assert_not_called()

    def test_a_broken_webhook_never_raises(self):
        self.urlopen.side_effect = OSError("unreachable")

        with self.assertLogs("accounts.alerts", level="WARNING"):
            alert("Delivery failed")

    def test_still_alerts_when_redis_is_down(self):
        with (
            mock.patch("accounts.alerts.cache") as fake_cache,
            self.assertLogs("accounts.alerts", level="CRITICAL"),
        ):
            fake_cache.add.side_effect = RedisConnectionError("down")
            alert("Delivery failed")

        self.urlopen.assert_called_once()
