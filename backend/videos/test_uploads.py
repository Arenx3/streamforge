from datetime import timedelta
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APITestCase

from videos.models import Video
from videos.tasks import abort_abandoned_uploads
from videos.uploads import MAX_PARTS, MIN_PART_BYTES, split_into_parts

Status = Video.Status
SIZE = MIN_PART_BYTES + 100
STORAGE_DOWN = EndpointConnectionError(endpoint_url="http://storage:9000")


def s3_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "S3Call")


class SplitIntoPartsTests(SimpleTestCase):
    def test_small_file_is_a_single_part(self):
        self.assertEqual(split_into_parts(100), [100])

    def test_exact_multiple_has_no_empty_part(self):
        self.assertEqual(split_into_parts(2 * MIN_PART_BYTES), [MIN_PART_BYTES] * 2)

    def test_last_part_holds_the_remainder(self):
        self.assertEqual(split_into_parts(SIZE), [MIN_PART_BYTES, 100])

    def test_huge_file_stays_within_part_limit(self):
        size = 5 * 1024**4 + 1

        parts = split_into_parts(size)

        self.assertLessEqual(len(parts), MAX_PARTS)
        self.assertEqual(sum(parts), size)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class UploadTestCase(APITestCase):
    create_url = reverse("video-list")

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="owner@example.com", email_verified_at=timezone.now())
        cls.other = users.create_user(email="other@example.com")

    def setUp(self):
        private = mock.patch("videos.uploads.s3_client")
        public = mock.patch("videos.uploads.s3_public_client")
        self.s3 = private.start().return_value
        self.public_s3 = public.start().return_value
        self.addCleanup(private.stop)
        self.addCleanup(public.stop)
        enqueue = mock.patch("videos.views.transcode_video")
        self.transcode = enqueue.start()
        self.addCleanup(enqueue.stop)
        self.s3.create_multipart_upload.return_value = {"UploadId": "upload-1"}
        self.s3.head_object.return_value = {"ContentLength": SIZE}
        self.public_s3.generate_presigned_url.side_effect = lambda _operation, Params, ExpiresIn: (
            f"https://s3.test/{Params['PartNumber']}"
        )
        self.client.force_authenticate(self.owner)

    def make_video(self, video_status=Status.PENDING_UPLOAD, owner=None, upload_id="upload-1"):
        video = Video(
            owner=owner or self.owner,
            title="Demo",
            status=video_status,
            size_bytes=SIZE,
            upload_id=upload_id,
        )
        video.source_key = f"videos/{video.id}/source"
        video.save()
        return video

    def stub_parts(self, *sizes):
        parts = [
            {"PartNumber": number, "Size": size, "ETag": f"etag-{number}"}
            for number, size in enumerate(sizes, start=1)
        ]
        self.s3.get_paginator.return_value.paginate.return_value = [{"Parts": parts}]

    def create(self, **overrides):
        payload = {"title": "Demo", "content_type": "video/mp4", "size_bytes": SIZE}
        return self.client.post(self.create_url, payload | overrides, format="json")

    def complete(self, video):
        return self.client.post(reverse("video-complete", args=[video.pk]))


class CreateUploadTests(UploadTestCase):
    def test_requires_authentication(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.create().status_code, status.HTTP_401_UNAUTHORIZED)
        self.s3.create_multipart_upload.assert_not_called()

    def test_requires_a_verified_email(self):
        unverified = get_user_model().objects.create_user(email="new@example.com")
        self.client.force_authenticate(unverified)

        response = self.create()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "email_not_verified")
        self.s3.create_multipart_upload.assert_not_called()
        self.assertFalse(Video.objects.exists())

    def test_creates_pending_video_with_one_url_per_part(self):
        response = self.create(description="Launch demo")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        video = Video.objects.get(pk=response.data["video"]["id"])
        self.assertEqual(video.owner, self.owner)
        self.assertEqual(video.status, Status.PENDING_UPLOAD)
        self.assertEqual(video.description, "Launch demo")
        self.assertEqual(video.source_key, f"videos/{video.id}/source")
        self.assertEqual(video.upload_id, "upload-1")
        self.assertEqual(
            response.data["upload"]["parts"],
            [
                {"part_number": 1, "size": MIN_PART_BYTES, "url": "https://s3.test/1"},
                {"part_number": 2, "size": 100, "url": "https://s3.test/2"},
            ],
        )

    def test_passes_content_type_to_storage(self):
        self.create(content_type="video/webm")

        kwargs = self.s3.create_multipart_upload.call_args.kwargs
        self.assertEqual(kwargs["ContentType"], "video/webm")

    def test_signs_the_exact_size_of_each_part(self):
        self.create()

        calls = self.public_s3.generate_presigned_url.call_args_list
        self.assertEqual(
            [call.kwargs["Params"]["ContentLength"] for call in calls], [MIN_PART_BYTES, 100]
        )

    def test_does_not_expose_storage_internals(self):
        video_data = self.create().data["video"]

        self.assertNotIn("upload_id", video_data)
        self.assertNotIn("source_key", video_data)

    def test_rejects_non_video_content_types(self):
        for content_type in ("application/pdf", "video/", "video/mp4\nx", "text/html;video/mp4"):
            with self.subTest(content_type):
                response = self.create(content_type=content_type)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("content_type", response.data)
        self.s3.create_multipart_upload.assert_not_called()

    @override_settings(VIDEO_UPLOAD_MAX_BYTES=1000)
    def test_rejects_invalid_sizes(self):
        for size in (0, -1, 1001, "abc"):
            with self.subTest(size):
                response = self.create(size_bytes=size)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("size_bytes", response.data)
        self.s3.create_multipart_upload.assert_not_called()

    @override_settings(VIDEO_UPLOAD_MAX_BYTES=1000)
    def test_accepts_file_at_the_limit(self):
        self.assertEqual(self.create(size_bytes=1000).status_code, status.HTTP_201_CREATED)

    def test_storage_failure_returns_503_and_creates_nothing(self):
        self.s3.create_multipart_upload.side_effect = STORAGE_DOWN

        with self.assertLogs("videos.views", level="ERROR"):
            response = self.create()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(Video.objects.exists())


class CompleteUploadTests(UploadTestCase):
    def test_assembles_parts_and_marks_uploaded(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES, 100)

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Status.UPLOADED)
        kwargs = self.s3.complete_multipart_upload.call_args.kwargs
        self.assertEqual(
            kwargs["MultipartUpload"]["Parts"],
            [{"PartNumber": 1, "ETag": "etag-1"}, {"PartNumber": 2, "ETag": "etag-2"}],
        )
        video.refresh_from_db()
        self.assertEqual(video.status, Status.UPLOADED)
        self.assertEqual(video.size_bytes, SIZE)
        self.assertEqual(video.upload_id, "")

    def test_enqueues_transcoding_once_uploaded(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES, 100)

        self.complete(video)

        self.transcode.delay.assert_called_once_with(str(video.id))

    def test_incomplete_upload_is_not_enqueued(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES)

        self.complete(video)

        self.transcode.delay.assert_not_called()

    def test_broker_failure_returns_503_and_retry_enqueues(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES, 100)
        self.transcode.delay.side_effect = OperationalError("broker down")

        with self.assertLogs("videos.views", level="ERROR"):
            response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        video.refresh_from_db()
        self.assertEqual(video.status, Status.UPLOADED)

        self.transcode.delay.side_effect = None

        self.assertEqual(self.complete(video).status_code, status.HTTP_200_OK)
        self.assertEqual(self.transcode.delay.call_count, 2)

    def test_video_already_processing_is_not_enqueued(self):
        video = self.make_video(video_status=Status.PROCESSING, upload_id="")

        self.assertEqual(self.complete(video).status_code, status.HTTP_200_OK)
        self.transcode.delay.assert_not_called()

    def test_requires_authentication(self):
        video = self.make_video()
        self.client.force_authenticate(None)

        self.assertEqual(self.complete(video).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cannot_complete_another_users_upload(self):
        video = self.make_video(owner=self.other)

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.s3.get_paginator.assert_not_called()
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PENDING_UPLOAD)

    def test_missing_part_is_a_conflict(self):
        video = self.make_video()
        parts = {"PartNumber": 2, "Size": SIZE, "ETag": "etag-2"}
        self.s3.get_paginator.return_value.paginate.return_value = [{"Parts": [parts]}]

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.s3.complete_multipart_upload.assert_not_called()
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PENDING_UPLOAD)

    def test_size_mismatch_is_a_conflict(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES)

        self.assertEqual(self.complete(video).status_code, status.HTTP_409_CONFLICT)
        self.s3.complete_multipart_upload.assert_not_called()

    def test_retry_after_lost_response_finishes_the_upload(self):
        video = self.make_video()
        self.s3.get_paginator.return_value.paginate.side_effect = s3_error("NoSuchUpload")

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Status.UPLOADED)

    def test_aborted_upload_is_a_conflict(self):
        video = self.make_video()
        self.s3.get_paginator.return_value.paginate.side_effect = s3_error("NoSuchUpload")
        self.s3.head_object.side_effect = s3_error("404")

        self.assertEqual(self.complete(video).status_code, status.HTTP_409_CONFLICT)

    def test_stored_object_of_wrong_size_is_a_conflict(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES, 100)
        self.s3.head_object.return_value = {"ContentLength": SIZE - 1}

        self.assertEqual(self.complete(video).status_code, status.HTTP_409_CONFLICT)
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PENDING_UPLOAD)

    def test_completing_twice_is_idempotent(self):
        video = self.make_video(video_status=Status.UPLOADED, upload_id="")

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.s3.get_paginator.assert_not_called()

    def test_losing_a_concurrent_completion_returns_the_winner(self):
        video = self.make_video()
        self.stub_parts(MIN_PART_BYTES, 100)

        def other_request_wins(**_kwargs):
            Video.objects.filter(pk=video.pk).update(status=Status.UPLOADED, upload_id="")
            return {"ContentLength": SIZE}

        self.s3.head_object.side_effect = other_request_wins

        response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Status.UPLOADED)

    def test_failed_upload_cannot_be_completed(self):
        video = self.make_video(video_status=Status.FAILED)

        self.assertEqual(self.complete(video).status_code, status.HTTP_409_CONFLICT)
        self.s3.get_paginator.assert_not_called()

    def test_storage_failure_returns_503_and_can_be_retried(self):
        video = self.make_video()
        self.s3.get_paginator.return_value.paginate.side_effect = STORAGE_DOWN

        with self.assertLogs("videos.views", level="ERROR"):
            response = self.complete(video)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PENDING_UPLOAD)
        self.assertEqual(video.upload_id, "upload-1")


@override_settings(VIDEO_UPLOAD_ABANDON_HOURS=24)
class AbandonedUploadTests(UploadTestCase):
    def make_old(self, **kwargs):
        video = self.make_video(**kwargs)
        old = timezone.now() - timedelta(hours=25)
        Video.objects.filter(pk=video.pk).update(created_at=old)
        return video

    def test_aborts_old_pending_upload(self):
        video = self.make_old()

        result = abort_abandoned_uploads()

        self.assertEqual(result, {"aborted": 1})
        self.s3.abort_multipart_upload.assert_called_once()
        self.s3.delete_object.assert_called_once()
        video.refresh_from_db()
        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.upload_id, "")
        self.assertIn("abandoned", video.error_message)

    def test_leaves_recent_upload_alone(self):
        video = self.make_video()

        self.assertEqual(abort_abandoned_uploads(), {"aborted": 0})
        self.s3.abort_multipart_upload.assert_not_called()
        video.refresh_from_db()
        self.assertEqual(video.status, Status.PENDING_UPLOAD)

    def test_leaves_finished_uploads_alone(self):
        for video_status in (Status.UPLOADED, Status.PROCESSING, Status.READY):
            self.make_old(video_status=video_status, upload_id="")

        self.assertEqual(abort_abandoned_uploads(), {"aborted": 0})
        self.s3.abort_multipart_upload.assert_not_called()

    def test_tolerates_upload_already_gone(self):
        video = self.make_old()
        self.s3.abort_multipart_upload.side_effect = s3_error("NoSuchUpload")

        self.assertEqual(abort_abandoned_uploads(), {"aborted": 1})
        video.refresh_from_db()
        self.assertEqual(video.upload_id, "")

    def test_storage_failure_is_retried_on_next_run(self):
        video = self.make_old()
        self.s3.abort_multipart_upload.side_effect = STORAGE_DOWN

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.assertEqual(abort_abandoned_uploads(), {"aborted": 0})
        video.refresh_from_db()
        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.upload_id, "upload-1")

        self.s3.abort_multipart_upload.side_effect = None

        self.assertEqual(abort_abandoned_uploads(), {"aborted": 1})
        video.refresh_from_db()
        self.assertEqual(video.upload_id, "")
