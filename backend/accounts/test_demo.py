import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.demo import DEMO_EMAIL, ensure_demo_video, get_demo_user
from accounts.tests import PASSWORD
from videos.demo import SAMPLE_TITLE, build_sample_command
from videos.models import Video
from videos.tasks import create_demo_video

User = get_user_model()
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM, DEMO_ENABLED=True)
class DemoLoginTests(APITestCase):
    url = reverse("auth-demo")

    def setUp(self):
        cache.clear()
        patcher = mock.patch("accounts.demo.create_demo_video")
        self.task = patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_a_working_session(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = self.client.get(reverse("auth-me"))
        self.assertEqual(me.data["email"], DEMO_EMAIL)
        self.assertTrue(me.data["email_verified"])

    def test_account_has_no_usable_password(self):
        self.client.post(self.url)

        self.assertFalse(User.objects.get(email=DEMO_EMAIL).has_usable_password())

    def test_queues_the_sample_only_when_empty(self):
        self.client.post(self.url)
        user = User.objects.get(email=DEMO_EMAIL)
        self.task.delay.assert_called_once_with(user.pk)

        Video.objects.create(owner=user, title="Sample")
        self.client.post(self.url)

        self.task.delay.assert_called_once()

    def test_broker_failure_still_logs_in(self):
        self.task.delay.side_effect = OperationalError("down")

        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_200_OK)

    def test_ignores_a_stale_authorization_header(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.invalid")

        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_200_OK)

    @override_settings(DEMO_ENABLED=False)
    def test_disabled_outside_local_use(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(User.objects.filter(email=DEMO_EMAIL).exists())

    def test_someone_who_claimed_the_address_loses_their_password(self):
        User.objects.create_user(email=DEMO_EMAIL, password=PASSWORD)

        self.client.post(self.url)

        user = User.objects.get(email=DEMO_EMAIL)
        self.assertFalse(user.check_password(PASSWORD))
        self.assertTrue(user.email_verified)


class LogoutTests(APITestCase):
    def test_revokes_the_refresh_token(self):
        user = User.objects.create_user(email="ana@example.com", password=PASSWORD)
        refresh = str(RefreshToken.for_user(user))

        response = self.client.post(reverse("auth-logout"), {"refresh": refresh}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        again = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(again.status_code, status.HTTP_401_UNAUTHORIZED)


class PublicConfigTests(APITestCase):
    @override_settings(
        DEMO_ENABLED=True, MAILBOX_URL="http://localhost:8025", VIDEO_UPLOAD_MAX_BYTES=1024
    )
    def test_exposes_only_what_the_app_needs(self):
        response = self.client.get(reverse("public-config"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "demo_enabled": True,
                "mailbox_url": "http://localhost:8025",
                "max_upload_bytes": 1024,
            },
        )


@override_settings(CACHES=LOCMEM, DEMO_ENABLED=True)
class SeedDemoTests(TestCase):
    def test_creates_the_account_and_queues_the_sample(self):
        with mock.patch("accounts.demo.create_demo_video") as task:
            call_command("seed_demo")

        user = User.objects.get(email=DEMO_EMAIL)
        task.delay.assert_called_once_with(user.pk)

    @override_settings(DEMO_ENABLED=False)
    def test_does_nothing_when_disabled(self):
        call_command("seed_demo")

        self.assertFalse(User.objects.filter(email=DEMO_EMAIL).exists())

    def test_never_stops_the_api_from_starting(self):
        with (
            mock.patch("accounts.management.commands.seed_demo.get_demo_user", side_effect=OSError),
            self.assertLogs("accounts.management.commands.seed_demo", level="ERROR"),
        ):
            call_command("seed_demo")


@override_settings(CACHES=LOCMEM)
class CreateDemoVideoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_demo_user()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setting = override_settings(STREAMFORGE_TMP_DIR=str(self.tmp))
        setting.enable()
        self.addCleanup(setting.disable)

    def patch(self, target):
        patcher = mock.patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def fake_ffmpeg(self, command, **_kwargs):
        Path(command[-1]).write_bytes(b"x" * 2048)

    def run_task(self):
        self.patch("videos.demo.subprocess.run").side_effect = self.fake_ffmpeg
        self.s3 = self.patch("videos.demo.s3_client").return_value
        self.transcode = self.patch("videos.tasks.transcode_video")
        create_demo_video(self.user.pk)

    def test_publishes_the_sample_and_starts_processing(self):
        self.run_task()

        video = Video.objects.get(owner=self.user)
        self.assertEqual(video.title, SAMPLE_TITLE)
        self.assertEqual(video.status, Video.Status.UPLOADED)
        self.assertEqual(video.size_bytes, 2048)
        self.assertEqual(self.s3.upload_file.call_args.args[2], video.source_key)
        self.transcode.delay.assert_called_once_with(str(video.id))

    def test_leaves_no_temp_files(self):
        self.run_task()

        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_skips_an_account_that_already_has_videos(self):
        Video.objects.create(owner=self.user, title="Own video")

        self.run_task()

        self.assertEqual(Video.objects.filter(owner=self.user).count(), 1)
        self.transcode.delay.assert_not_called()

    def test_concurrent_runs_create_one_video(self):
        cache.add("demo-video-lock", 1, 60)

        self.run_task()

        self.assertFalse(Video.objects.filter(owner=self.user).exists())

    def test_failed_generation_creates_nothing_and_releases_the_lock(self):
        self.patch("videos.demo.subprocess.run").side_effect = subprocess.CalledProcessError(1, "x")

        with self.assertRaises(subprocess.CalledProcessError):
            create_demo_video(self.user.pk)

        self.assertFalse(Video.objects.filter(owner=self.user).exists())
        self.assertTrue(cache.add("demo-video-lock", 1, 60))
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_ensure_skips_when_there_is_a_video(self):
        Video.objects.create(owner=self.user, title="Own video")

        with mock.patch("accounts.demo.create_demo_video") as task:
            ensure_demo_video(self.user)

        task.delay.assert_not_called()

    @skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_real_ffmpeg_builds_a_playable_sample(self):
        target = self.tmp / "sample.mp4"
        command = build_sample_command(target)
        command[command.index("-i") + 1] = command[command.index("-i") + 1].replace(
            "s=1920x1080", "s=320x180"
        )

        subprocess.run(command, check=True, timeout=120)

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type"]
            + ["-of", "csv=p=0", str(target)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(sorted(probe.stdout.split()), ["audio", "video"])
