from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from videos.models import InvalidTransition, Video

Status = Video.Status

# One call per target status, used to drive every (source, target) pair.
ACTIONS = {
    Status.UPLOADED: lambda video: video.mark_uploaded(size_bytes=1024),
    Status.PROCESSING: lambda video: video.start_processing(),
    Status.READY: lambda video: video.mark_ready(hls_master_key="videos/x/master.m3u8"),
    Status.FAILED: lambda video: video.mark_failed("ffmpeg exited with code 1"),
}


class VideoStateMachineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner", password="x")

    def make_video(self, status=Status.PENDING_UPLOAD):
        return Video.objects.create(owner=self.owner, title="Demo", status=status)

    def test_new_video_waits_for_upload(self):
        video = self.make_video()

        self.assertEqual(video.status, Status.PENDING_UPLOAD)
        self.assertEqual(video.progress, 0)
        self.assertIsNone(video.processed_at)

    def test_full_lifecycle_persists_each_step(self):
        video = self.make_video()

        video.mark_uploaded(size_bytes=5_000_000)
        video.start_processing()
        video.update_progress(40)
        video.mark_ready(
            hls_master_key="videos/abc/master.m3u8",
            thumbnail_key="videos/abc/thumb.jpg",
            duration_seconds=12.5,
            width=1920,
            height=1080,
        )

        video.refresh_from_db()
        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.progress, 100)
        self.assertEqual(video.size_bytes, 5_000_000)
        self.assertEqual(video.hls_master_key, "videos/abc/master.m3u8")
        self.assertEqual((video.width, video.height), (1920, 1080))
        self.assertIsNotNone(video.processed_at)

    def test_every_source_and_target_pair(self):
        for source in Status:
            for target, action in ACTIONS.items():
                allowed = target in Video.TRANSITIONS[source]
                with self.subTest(source=source, target=target, allowed=allowed):
                    video = self.make_video(status=source)

                    if allowed:
                        action(video)
                        expected = target
                    else:
                        with self.assertRaises(InvalidTransition):
                            action(video)
                        expected = source

                    video.refresh_from_db()
                    self.assertEqual(video.status, expected)

    def test_failure_records_error_message(self):
        video = self.make_video(status=Status.PROCESSING)

        video.mark_failed("ffmpeg exited with code 1")

        video.refresh_from_db()
        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.error_message, "ffmpeg exited with code 1")

    def test_stale_instance_cannot_overwrite_newer_status(self):
        video = self.make_video(status=Status.UPLOADED)
        stale_copy = Video.objects.get(pk=video.pk)

        video.start_processing()

        with self.assertRaises(InvalidTransition):
            stale_copy.start_processing()
        stale_copy.refresh_from_db()
        self.assertEqual(stale_copy.status, Status.PROCESSING)

    def test_unsaved_video_cannot_transition(self):
        video = Video(owner=self.owner, title="Unsaved")

        with self.assertRaises(ValueError):
            video.mark_uploaded()

    def test_progress_only_updates_while_processing(self):
        video = self.make_video(status=Status.UPLOADED)

        with self.assertRaises(InvalidTransition):
            video.update_progress(10)

    def test_progress_rejects_out_of_range_values(self):
        video = self.make_video(status=Status.PROCESSING)

        for value in (-1, 101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                video.update_progress(value)

    def test_database_rejects_progress_above_100(self):
        video = self.make_video()

        with self.assertRaises(IntegrityError):
            Video.objects.filter(pk=video.pk).update(progress=101)
