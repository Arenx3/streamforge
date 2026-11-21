import time
import uuid
from datetime import timedelta
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from videos.heartbeat import Heartbeat
from videos.models import InvalidTransition, Video
from videos.tasks import recover_stuck_videos, transcode_video
from videos.test_transcoding import MEDIA, TaskTestCase, fake_hls
from videos.transcoding import TranscodeError, readable_ffmpeg_error

Status = Video.Status
STORAGE_DOWN = EndpointConnectionError(endpoint_url="http://storage:9000")


def s3_error(code, http_status):
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": http_status},
        },
        "S3Call",
    )


def ago(**delta):
    return timezone.now() - timedelta(**delta)


class ReadableErrorTests(SimpleTestCase):
    def test_keeps_the_last_lines(self):
        log = "first\nsecond\nthird\nfourth\n"

        self.assertEqual(
            readable_ffmpeg_error(log),
            "FFmpeg could not process this file: second | third | fourth",
        )

    def test_hides_memory_addresses_and_paths(self):
        log = (
            "[h264 @ 0x55d0c1a2b3c0] Invalid NAL unit size\n"
            "/tmp/streamforge/4f1c/1/source: Invalid data found\n"
            "Conversion failed!\n"
        )

        message = readable_ffmpeg_error(log)

        self.assertNotIn("0x", message)
        self.assertNotIn("/tmp", message)
        self.assertIn("h264: Invalid NAL unit size", message)
        self.assertIn("<file>: Invalid data found", message)

    def test_removes_control_characters(self):
        self.assertNotIn("\x1b", readable_ffmpeg_error("bad \x1b[31mframe\x00\n"))

    def test_limits_the_length(self):
        self.assertLess(len(readable_ffmpeg_error("x" * 5000)), 400)

    def test_handles_an_empty_log(self):
        self.assertEqual(readable_ffmpeg_error(""), "FFmpeg could not process this file.")


class ClaimTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(email="owner@example.com")

    def make_video(self, **extra):
        defaults = {
            "status": Status.PROCESSING,
            "processing_task_id": "task-1",
            "processing_attempts": 1,
        }
        return Video.objects.create(owner=self.owner, title="Demo", **(defaults | extra))

    def test_duplicate_of_a_live_run_is_refused(self):
        video = self.make_video(heartbeat_at=timezone.now())

        self.assertFalse(video.claim_for_processing("task-1"))
        video.refresh_from_db()
        self.assertEqual(video.processing_attempts, 1)

    def test_run_without_a_recent_heartbeat_can_be_resumed(self):
        video = self.make_video(heartbeat_at=ago(hours=1))

        self.assertTrue(video.claim_for_processing("task-1"))
        video.refresh_from_db()
        self.assertEqual(video.processing_attempts, 2)
        self.assertGreater(video.heartbeat_at, ago(minutes=1))

    def test_run_that_never_beat_can_be_resumed(self):
        video = self.make_video(heartbeat_at=None)

        self.assertTrue(video.claim_for_processing("task-1"))

    def test_another_task_cannot_claim(self):
        video = self.make_video(heartbeat_at=ago(hours=1))

        self.assertFalse(video.claim_for_processing("task-2"))

    def test_only_one_of_two_simultaneous_claims_wins(self):
        video = self.make_video(heartbeat_at=ago(hours=1))
        same_row = Video.objects.get(pk=video.pk)

        self.assertTrue(video.claim_for_processing("task-1"))
        self.assertFalse(same_row.claim_for_processing("task-1"))

    def test_first_start_records_a_heartbeat(self):
        video = self.make_video(status=Status.UPLOADED, processing_attempts=0)

        self.assertTrue(video.claim_for_processing("task-1"))
        self.assertIsNotNone(video.heartbeat_at)

    def test_superseded_run_cannot_write(self):
        video = self.make_video(heartbeat_at=ago(hours=1))
        stale = Video.objects.get(pk=video.pk)
        video.claim_for_processing("task-1")

        for write in (
            lambda: stale.update_progress(50),
            lambda: stale.mark_ready(hls_master_key="k"),
            lambda: stale.mark_failed("late failure"),
        ):
            with self.assertRaises(InvalidTransition):
                write()
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PROCESSING)


class HeartbeatTests(TransactionTestCase):
    def make_video(self, **extra):
        owner = get_user_model().objects.create_user(email=f"{uuid.uuid4()}@example.com")
        return Video.objects.create(
            owner=owner,
            title="Demo",
            status=Status.PROCESSING,
            processing_attempts=1,
            **extra,
        )

    def test_keeps_the_timestamp_fresh(self):
        old = ago(hours=1)
        video = self.make_video(heartbeat_at=old)

        with mock.patch("videos.heartbeat.HEARTBEAT_INTERVAL_SECONDS", 0.02), Heartbeat(video):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if Video.objects.get(pk=video.pk).heartbeat_at > old:
                    break
                time.sleep(0.02)

        self.assertGreater(Video.objects.get(pk=video.pk).heartbeat_at, old)

    def test_stops_counting_once_superseded(self):
        old = ago(hours=1)
        video = self.make_video(heartbeat_at=old)
        Video.objects.filter(pk=video.pk).update(processing_attempts=2)

        with mock.patch("videos.heartbeat.HEARTBEAT_INTERVAL_SECONDS", 0.02), Heartbeat(video):
            time.sleep(0.2)

        self.assertEqual(Video.objects.get(pk=video.pk).heartbeat_at, old)


class PipelineTestCase(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.patch("videos.tasks.probe", return_value=MEDIA)
        self.patch("videos.tasks.require_free_space")
        self.patch("videos.tasks.extract_thumbnail", return_value=False)
        self.run_ffmpeg = self.patch("videos.tasks.run_ffmpeg", side_effect=fake_hls)

    def stub_stored(self, video):
        keys = [f"videos/{video.id}/hls/master.m3u8", f"videos/{video.id}/hls/360p/seg_00000.ts"]
        self.s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": key} for key in keys]}
        ]
        self.s3.delete_objects.return_value = {}
        return keys

    def deleted_keys(self):
        return [
            item["Key"]
            for call in self.s3.delete_objects.call_args_list
            for item in call.kwargs["Delete"]["Objects"]
        ]


class TransientStorageTests(PipelineTestCase):
    def test_retries_a_failed_download(self):
        video = self.make_video()
        self.s3.download_file.side_effect = [STORAGE_DOWN, None]

        self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(self.s3.download_file.call_count, 2)

    def test_retries_a_failed_upload(self):
        video = self.make_video()
        self.s3.upload_file.side_effect = [s3_error("SlowDown", 503)] + [None] * 10

        self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(self.s3.upload_file.call_count, 6)

    def test_missing_source_is_not_retried(self):
        video = self.make_video()
        self.s3.download_file.side_effect = s3_error("404", 404)

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.s3.download_file.assert_called_once()

    def test_storage_that_stays_down_fails_the_video(self):
        video = self.make_video()
        self.s3.download_file.side_effect = STORAGE_DOWN

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(self.s3.download_file.call_count, 4)
        self.assertIn("Storage", video.error_message)
        self.assertNotIn("storage:9000", video.error_message)
        self.assertFalse(self.workdir(video).exists())


class FailureMessageTests(PipelineTestCase):
    def test_ffmpeg_details_reach_the_user(self):
        video = self.make_video()
        self.run_ffmpeg.side_effect = TranscodeError("FFmpeg could not process this file: bad")

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.error_message, "FFmpeg could not process this file: bad")

    def test_time_limit_is_reported_and_cleaned_up(self):
        video = self.make_video()
        self.run_ffmpeg.side_effect = SoftTimeLimitExceeded()

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertIn("too long", video.error_message)
        self.assertFalse(self.workdir(video).exists())


class PartialArtifactTests(PipelineTestCase):
    def test_failure_removes_hls_and_thumbnail(self):
        video = self.make_video()
        keys = self.stub_stored(video)
        self.s3.upload_file.side_effect = [None, RuntimeError("disk error")]

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(
            self.s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"],
            f"videos/{video.id}/hls/",
        )
        self.assertCountEqual(self.deleted_keys(), keys)
        self.assertEqual(
            self.s3.delete_object.call_args.kwargs["Key"], f"videos/{video.id}/thumbnail.webp"
        )

    def test_failure_keeps_the_original(self):
        video = self.make_video()
        self.stub_stored(video)
        self.run_ffmpeg.side_effect = TranscodeError("bad file")

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertNotIn(video.source_key, self.deleted_keys())
        deleted_one_by_one = [c.kwargs["Key"] for c in self.s3.delete_object.call_args_list]
        self.assertNotIn(video.source_key, deleted_one_by_one)

    def test_cleanup_failure_still_leaves_the_video_failed(self):
        video = self.make_video()
        self.s3.get_paginator.side_effect = STORAGE_DOWN
        self.run_ffmpeg.side_effect = TranscodeError("bad file")

        with self.assertLogs("videos.tasks", level="ERROR") as logs:
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertTrue(any("partial files" in line for line in logs.output))

    def test_deleted_video_loses_its_partial_files(self):
        video = self.make_video()
        keys = self.stub_stored(video)

        def delete_during_run(command, *, cwd, timeout, on_progress=None):
            fake_hls(command, cwd=cwd, timeout=timeout)
            Video.objects.filter(pk=video.pk).delete()

        self.run_ffmpeg.side_effect = delete_during_run

        with self.assertLogs("videos.tasks", level="WARNING"):
            transcode_video.apply(args=[str(video.id)], task_id="task-1").get()

        self.assertCountEqual(self.deleted_keys(), keys)

    def test_giving_up_after_crashes_removes_partial_files(self):
        video = self.make_video(
            video_status=Status.PROCESSING,
            processing_task_id="task-1",
            processing_attempts=Video.MAX_PROCESSING_ATTEMPTS,
        )
        keys = self.stub_stored(video)

        self.run_task(video, task_id="task-1")

        self.assertEqual(video.status, Status.FAILED)
        self.assertCountEqual(self.deleted_keys(), keys)


class SupersededRunTests(PipelineTestCase):
    def take_over(self, video, newer_file=None):
        def encode(command, *, cwd, timeout, on_progress=None):
            fake_hls(command, cwd=cwd, timeout=timeout)
            if newer_file:
                newer_file.parent.mkdir(parents=True)
                newer_file.write_text("newer run")
            Video.objects.filter(pk=video.pk).update(processing_attempts=2)

        self.run_ffmpeg.side_effect = encode

    def test_keeps_the_files_of_the_newer_run_in_storage(self):
        video = self.make_video()
        self.stub_stored(video)
        self.take_over(video)

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertEqual(video.status, Status.PROCESSING)
        self.s3.delete_objects.assert_not_called()

    def test_keeps_the_directory_of_the_newer_run(self):
        video = self.make_video()
        newer_file = self.workdir(video) / "2" / "source"
        self.take_over(video, newer_file)

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertTrue(newer_file.exists())
        self.assertFalse((self.workdir(video) / "1").exists())

    def test_duplicate_delivery_leaves_a_live_run_untouched(self):
        video = self.make_video(
            video_status=Status.PROCESSING,
            processing_task_id="task-1",
            processing_attempts=1,
            heartbeat_at=timezone.now(),
        )
        running_file = self.workdir(video) / "1" / "source"
        running_file.parent.mkdir(parents=True)
        running_file.write_text("in progress")

        result = self.run_task(video, task_id="task-1")

        self.assertTrue(result["skipped"])
        self.assertTrue(running_file.exists())
        self.s3.download_file.assert_not_called()


class RecoverStuckVideosTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.enqueue = self.patch("videos.tasks.transcode_video.apply_async")

    def make_processing(self, **extra):
        defaults = {
            "video_status": Status.PROCESSING,
            "processing_task_id": "task-1",
            "processing_attempts": 1,
        }
        return self.make_video(**(defaults | extra))

    def test_restarts_a_run_whose_worker_died(self):
        video = self.make_processing(heartbeat_at=ago(hours=1))

        result = recover_stuck_videos()

        self.assertEqual(result["recovered"], 1)
        self.enqueue.assert_called_once_with(args=[str(video.id)], task_id="task-1")

    def test_restarts_a_run_that_never_beat(self):
        self.make_processing(heartbeat_at=None)

        self.assertEqual(recover_stuck_videos()["recovered"], 1)

    def test_leaves_a_live_run_alone(self):
        self.make_processing(heartbeat_at=timezone.now())

        self.assertEqual(recover_stuck_videos()["recovered"], 0)
        self.enqueue.assert_not_called()

    def test_requeues_an_upload_whose_message_was_lost(self):
        video = self.make_video(video_status=Status.UPLOADED)
        Video.objects.filter(pk=video.pk).update(updated_at=ago(hours=1))

        self.assertEqual(recover_stuck_videos()["recovered"], 1)
        self.enqueue.assert_called_once_with(args=[str(video.id)], task_id=None)

    def test_leaves_a_recent_upload_in_the_queue(self):
        self.make_video(video_status=Status.UPLOADED)

        self.assertEqual(recover_stuck_videos()["recovered"], 0)

    def test_does_not_flood_a_busy_queue(self):
        self.make_processing(heartbeat_at=ago(hours=1))

        recover_stuck_videos()
        recover_stuck_videos()

        self.enqueue.assert_called_once()

    def test_ignores_finished_videos(self):
        for video_status in (Status.PENDING_UPLOAD, Status.READY, Status.FAILED):
            video = self.make_video(video_status=video_status, processing_task_id="task-1")
            Video.objects.filter(pk=video.pk).update(updated_at=ago(hours=1))

        self.assertEqual(recover_stuck_videos()["recovered"], 0)

    def test_removes_only_orphaned_directories(self):
        finished = self.make_video(video_status=Status.READY)
        running = self.make_processing(heartbeat_at=timezone.now())
        deleted_id = uuid.uuid4()
        for name in (finished.id, running.id, deleted_id, "not-a-video"):
            (self.tmp / str(name) / "1").mkdir(parents=True)

        result = recover_stuck_videos()

        self.assertEqual(result["orphaned_dirs_removed"], 2)
        self.assertFalse((self.tmp / str(finished.id)).exists())
        self.assertFalse((self.tmp / str(deleted_id)).exists())
        self.assertTrue((self.tmp / str(running.id)).exists())
        self.assertTrue((self.tmp / "not-a-video").exists())
