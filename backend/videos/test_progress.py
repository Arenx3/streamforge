import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APITestCase

from videos.models import InvalidTransition, Video
from videos.progress import ProgressReporter, live_progress
from videos.tasks import transcode_video
from videos.test_transcoding import MEDIA, TaskTestCase, fake_hls
from videos.transcoding import TranscodeError, parse_progress, run_ffmpeg

Status = Video.Status
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ParseProgressTests(SimpleTestCase):
    def test_yields_encoded_seconds(self):
        lines = [
            "frame=11\n",
            "bitrate=N/A\n",
            "out_time_us=0\n",
            "out_time_ms=0\n",
            "out_time=00:00:00.000000\n",
            "progress=continue\n",
            "out_time_us=2500000\n",
            "progress=end\n",
        ]

        self.assertEqual(list(parse_progress(lines)), [0.0, 2.5])

    def test_ignores_unusable_values(self):
        lines = ["out_time_us=N/A\n", "out_time_us=\n", "garbage\n", "out_time_us=1000000\n"]

        self.assertEqual(list(parse_progress(lines)), [1.0])

    def test_clamps_negative_timestamps(self):
        self.assertEqual(list(parse_progress(["out_time_us=-5000\n"])), [0.0])


@override_settings(CACHES=LOCMEM)
class ProgressReporterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(email="owner@example.com")

    def setUp(self):
        cache.clear()
        self.video = Video.objects.create(owner=self.owner, title="Demo", status=Status.PROCESSING)
        self.clock = FakeClock()
        self.reporter = ProgressReporter(self.video, clock=self.clock)

    def test_never_goes_backwards(self):
        published = []
        for percent in (10, 30, 20, 40):
            self.clock.advance(2)
            self.reporter.report(percent)
            published.append(live_progress(self.video))

        self.assertEqual(published, [10, 30, 30, 40])

    def test_publishes_at_most_once_per_second(self):
        with mock.patch("videos.progress.cache") as fake_cache:
            for percent in range(1, 51):
                self.clock.advance(0.1)
                self.reporter.report(percent)

        self.assertLessEqual(fake_cache.set.call_count, 6)
        self.assertGreaterEqual(fake_cache.set.call_count, 4)

    def test_persists_only_every_five_percent(self):
        update = mock.patch.object(self.video, "update_progress", wraps=self.video.update_progress)
        with update as update_progress:
            for percent in range(1, 100):
                self.reporter.report(percent)

        self.assertEqual(update_progress.call_count, 19)
        self.video.refresh_from_db()
        self.assertEqual(self.video.progress, 95)

    def test_100_is_reserved_for_ready(self):
        self.reporter.report(100)

        self.assertEqual(self.reporter.progress, 99)
        self.video.refresh_from_db()
        self.assertEqual(self.video.progress, 99)

    def test_phases_fill_the_bar_in_order(self):
        steps = [
            (self.reporter.downloaded, (), 5),
            (self.reporter.transcoded, (0, 100), 5),
            (self.reporter.transcoded, (50, 100), 47),
            (self.reporter.transcoded, (100, 100), 90),
            (self.reporter.transcoded, (200, 100), 90),
            (self.reporter.uploaded, (1, 2), 94),
            (self.reporter.uploaded, (2, 2), 99),
        ]
        for action, args, expected in steps:
            with self.subTest(action=action.__name__, args=args):
                action(*args)

                self.assertEqual(self.reporter.progress, expected)

    def test_redis_failure_does_not_stop_the_run(self):
        with mock.patch("videos.progress.cache") as fake_cache:
            fake_cache.set.side_effect = RedisConnectionError("redis down")
            with self.assertLogs("videos.progress", level="WARNING"):
                self.reporter.report(10)

        self.video.refresh_from_db()
        self.assertEqual(self.video.progress, 10)

    def test_database_failure_does_not_stop_the_run(self):
        with (
            mock.patch.object(
                self.video, "update_progress", side_effect=OperationalError("connection lost")
            ),
            mock.patch("videos.progress.close_old_connections") as close,
            self.assertLogs("videos.progress", level="WARNING"),
        ):
            self.reporter.report(10)

        close.assert_called_once()
        self.assertEqual(self.reporter.progress, 10)

    def test_deleted_video_stops_the_run(self):
        Video.objects.filter(pk=self.video.pk).delete()

        with self.assertRaises(InvalidTransition):
            self.reporter.report(10)

    def test_finish_clears_the_live_value(self):
        self.reporter.report(40)

        self.reporter.finish()

        self.video.progress = 0
        self.assertEqual(live_progress(self.video), 0)

    def test_new_run_resets_a_stale_live_value(self):
        self.reporter.report(3)
        self.assertEqual(live_progress(self.video), 3)

        ProgressReporter(self.video, clock=self.clock)

        self.assertEqual(live_progress(self.video), 0)


@override_settings(CACHES=LOCMEM)
class VideoStatusEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="owner@example.com")
        cls.other = users.create_user(email="other@example.com")

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.owner)

    def make_video(self, video_status=Status.PROCESSING, owner=None, **extra):
        return Video.objects.create(
            owner=owner or self.owner, title="Demo", status=video_status, **extra
        )

    def get_status(self, video):
        return self.client.get(reverse("video-status", args=[video.pk]))

    def test_requires_authentication(self):
        video = self.make_video()
        self.client.force_authenticate(None)

        self.assertEqual(self.get_status(video).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cannot_read_another_users_video(self):
        video = self.make_video(owner=self.other)

        self.assertEqual(self.get_status(video).status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_status_progress_and_error(self):
        video = self.make_video(Status.FAILED, progress=45, error_message="Bad file.")

        response = self.get_status(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data, {"status": "FAILED", "progress": 45, "error_message": "Bad file."}
        )

    def test_uses_the_fresher_live_value_while_processing(self):
        video = self.make_video(progress=40)
        reporter = ProgressReporter(video)
        reporter.progress = 40
        reporter.report(43)

        self.assertEqual(self.get_status(video).data["progress"], 43)

    def test_never_shows_less_than_the_saved_value(self):
        video = self.make_video(progress=40)
        ProgressReporter(video)

        self.assertEqual(self.get_status(video).data["progress"], 40)

    def test_ignores_live_value_once_finished(self):
        video = self.make_video(progress=40)
        ProgressReporter(video).report(60)
        Video.objects.filter(pk=video.pk).update(status=Status.READY, progress=100)

        self.assertEqual(self.get_status(video).data["progress"], 100)

    def test_falls_back_to_the_database_when_redis_is_down(self):
        video = self.make_video(progress=40)

        with mock.patch("videos.progress.cache") as fake_cache:
            fake_cache.get.side_effect = RedisConnectionError("redis down")
            response = self.get_status(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["progress"], 40)


class RunFfmpegTests(SimpleTestCase):
    """Uses a small Python process in place of FFmpeg to control its output and timing."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.cwd = tmp / "job" / "hls"
        self.cwd.mkdir(parents=True)
        env = mock.patch("videos.transcoding._sandbox_env", return_value=dict(os.environ))
        env.start()
        self.addCleanup(env.stop)

    def run_script(self, code, **kwargs):
        kwargs.setdefault("timeout", 20)
        return run_ffmpeg([sys.executable, "-c", code], cwd=self.cwd, **kwargs)

    def test_streams_progress_to_the_callback(self):
        seen = []
        code = "print('out_time_us=1000000', flush=True); print('out_time_us=2000000', flush=True)"

        self.run_script(code, on_progress=seen.append)

        self.assertEqual(seen, [1.0, 2.0])

    def test_runs_without_a_callback(self):
        self.run_script("print('ignored')")

    def test_non_zero_exit_raises(self):
        with (
            self.assertLogs("videos.transcoding", level="ERROR"),
            self.assertRaises(TranscodeError),
        ):
            self.run_script("import sys; sys.exit(3)")

    def test_timeout_kills_the_process(self):
        started = time.monotonic()

        with self.assertRaisesMessage(TranscodeError, "too long"):
            self.run_script("import time; time.sleep(30)", timeout=0.5)

        self.assertLess(time.monotonic() - started, 10)

    def test_callback_error_kills_the_process(self):
        code = "import time; print('out_time_us=1000000', flush=True); time.sleep(30)"
        started = time.monotonic()

        def stop(_seconds):
            raise InvalidTransition("video deleted")

        with self.assertRaises(InvalidTransition):
            self.run_script(code, on_progress=stop)

        self.assertLess(time.monotonic() - started, 10)


class TranscodeProgressTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.patch("videos.tasks.probe", return_value=MEDIA)
        self.patch("videos.tasks.require_free_space")
        self.patch("videos.tasks.extract_thumbnail", return_value=False)
        self.run_ffmpeg = self.patch("videos.tasks.run_ffmpeg", side_effect=self.encode)

    def encode(self, command, *, cwd, timeout, on_progress=None):
        fake_hls(command, cwd=cwd, timeout=timeout)
        for seconds in (0, 3, 6, 9, MEDIA.duration):
            on_progress(seconds)

    def test_reaches_100_when_ready_and_clears_live_value(self):
        video = self.make_video()

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.progress, 100)
        self.assertIsNone(cache.get(f"video-progress:{video.pk}"))

    def test_saved_progress_only_moves_forward(self):
        video = self.make_video()
        saved = []
        original = Video.update_progress

        def record(instance, progress):
            saved.append(progress)
            original(instance, progress)

        with (
            mock.patch.object(Video, "update_progress", record),
            self.assertLogs("videos.tasks", level="WARNING"),
        ):
            self.run_task(video)

        self.assertGreater(len(saved), 3)
        self.assertEqual(saved, sorted(saved))
        self.assertLess(saved[-1], 100)

    def test_failure_keeps_the_last_saved_progress(self):
        video = self.make_video()

        def encode_then_fail(command, *, cwd, timeout, on_progress=None):
            on_progress(MEDIA.duration / 2)
            raise TranscodeError("FFmpeg could not process this file.")

        self.run_ffmpeg.side_effect = encode_then_fail

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.progress, 47)

    def test_deleting_the_video_stops_processing(self):
        video = self.make_video()

        def delete_mid_run(_command, *, cwd, timeout, on_progress=None):
            Video.objects.filter(pk=video.pk).delete()
            on_progress(6)

        self.run_ffmpeg.side_effect = delete_mid_run

        with self.assertLogs("videos.tasks", level="WARNING"):
            transcode_video.apply(args=[str(video.id)], task_id="task-1").get()

        self.s3.upload_file.assert_not_called()
        self.assertFalse(self.workdir(video).exists())
