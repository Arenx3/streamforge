import uuid
from datetime import timedelta
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APITestCase

from videos.models import Video
from videos.playback import make_playback_token, read_playback_token
from videos.progress import ProgressReporter
from videos.serializers import VideoUpdateSerializer
from videos.tasks import delete_video_files

Status = Video.Status
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGE_DOWN = EndpointConnectionError(endpoint_url="http://storage:9000")
PRIVATE_FIELDS = {
    "owner",
    "source_key",
    "upload_id",
    "hls_master_key",
    "thumbnail_key",
    "processing_task_id",
    "processing_attempts",
    "heartbeat_at",
}


def s3_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "S3Call")


def fake_presign(_operation, Params, ExpiresIn):
    return f"https://s3.test/{Params['Key']}?signature=abc"


@override_settings(CACHES=LOCMEM)
class VideoApiTestCase(APITestCase):
    list_url = reverse("video-list")

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="owner@example.com")
        cls.other = users.create_user(email="other@example.com")

    def setUp(self):
        cache.clear()
        self.make_client = self.patch("videos.serializers.s3_public_client")
        self.make_client.return_value.generate_presigned_url.side_effect = fake_presign
        self.delete_files = self.patch("videos.views.delete_video_files")
        self.client.force_authenticate(self.owner)

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_video(self, video_status=Status.READY, owner=None, **extra):
        video = Video(owner=owner or self.owner, status=video_status, **{"title": "Demo"} | extra)
        if video_status == Status.READY:
            video.hls_master_key = f"videos/{video.id}/hls/master.m3u8"
            video.thumbnail_key = f"videos/{video.id}/thumbnail.webp"
        video.save()
        return video

    def detail_url(self, video):
        return reverse("video-detail", args=[video.pk])


class ListVideosTests(VideoApiTestCase):
    def test_requires_authentication(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_only_lists_your_videos(self):
        mine = self.make_video()
        self.make_video(owner=self.other)

        response = self.client.get(self.list_url)

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(mine.id))

    def test_newest_first(self):
        old = self.make_video(title="Old")
        new = self.make_video(title="New")
        Video.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1))

        ids = [item["id"] for item in self.client.get(self.list_url).data["results"]]

        self.assertEqual(ids, [str(new.id), str(old.id)])

    def test_is_paginated(self):
        for _ in range(3):
            self.make_video()

        first = self.client.get(self.list_url, {"page_size": 2})
        second = self.client.get(self.list_url, {"page_size": 2, "page": 2})

        self.assertEqual(first.data["count"], 3)
        self.assertEqual(len(first.data["results"]), 2)
        self.assertIsNotNone(first.data["next"])
        self.assertEqual(len(second.data["results"]), 1)
        self.assertIsNone(second.data["next"])

    def test_page_size_is_capped(self):
        for _ in range(3):
            self.make_video()

        with mock.patch("videos.views.VideoPagination.max_page_size", 2):
            response = self.client.get(self.list_url, {"page_size": 1000})

        self.assertEqual(len(response.data["results"]), 2)

    def test_filters_by_status(self):
        ready = self.make_video(Status.READY)
        self.make_video(Status.PROCESSING)

        response = self.client.get(self.list_url, {"status": "READY"})

        self.assertEqual([item["id"] for item in response.data["results"]], [str(ready.id)])

    def test_filters_by_several_statuses(self):
        queued = self.make_video(Status.UPLOADED)
        running = self.make_video(Status.PROCESSING)
        self.make_video(Status.READY)

        response = self.client.get(self.list_url, {"status": "UPLOADED,PROCESSING"})

        self.assertCountEqual(
            [item["id"] for item in response.data["results"]], [str(queued.id), str(running.id)]
        )

    def test_rejects_unknown_status(self):
        response = self.client.get(self.list_url, {"status": "NOPE"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

    def test_does_not_expose_internals(self):
        self.make_video()

        item = self.client.get(self.list_url).data["results"][0]

        self.assertFalse(PRIVATE_FIELDS & set(item))
        self.assertNotIn("playback_url", item)

    def test_says_why_a_video_failed(self):
        self.make_video(Status.FAILED, error_message="Upload abandoned before it was completed.")

        row = self.client.get(self.list_url).data["results"][0]

        self.assertEqual(row["error_message"], "Upload abandoned before it was completed.")

    def test_page_shares_one_storage_client(self):
        for _ in range(3):
            self.make_video()

        response = self.client.get(self.list_url)

        self.assertTrue(all(item["thumbnail_url"] for item in response.data["results"]))
        self.make_client.assert_called_once()


class VideoSummaryTests(VideoApiTestCase):
    url = reverse("video-summary")

    def test_counts_your_videos_by_status(self):
        self.make_video(Status.READY)
        self.make_video(Status.READY)
        self.make_video(Status.FAILED)
        self.make_video(Status.READY, owner=self.other)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"PENDING_UPLOAD": 0, "UPLOADED": 0, "PROCESSING": 0, "READY": 2, "FAILED": 1},
        )

    def test_requires_authentication(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)


class VideoDetailTests(VideoApiTestCase):
    def test_returns_metadata_and_urls(self):
        video = self.make_video(duration_seconds=12.5, width=1920, height=1080, size_bytes=99)

        response = self.client.get(self.detail_url(video))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["duration_seconds"], 12.5)
        self.assertEqual((response.data["width"], response.data["height"]), (1920, 1080))
        self.assertIn(f"videos/{video.id}/thumbnail.webp", response.data["thumbnail_url"])
        self.assertIn("/api/playback/", response.data["playback_url"])
        self.assertTrue(response.data["playback_url"].endswith("/master.m3u8"))
        self.assertTrue(response.data["playback_url"].startswith("http"))

    def test_does_not_expose_internals(self):
        video = self.make_video()

        self.assertFalse(PRIVATE_FIELDS & set(self.client.get(self.detail_url(video)).data))

    def test_no_urls_until_ready(self):
        for video_status in (Status.PENDING_UPLOAD, Status.PROCESSING, Status.FAILED):
            with self.subTest(video_status):
                video = self.make_video(video_status)

                response = self.client.get(self.detail_url(video))

                self.assertIsNone(response.data["playback_url"])
                self.assertIsNone(response.data["thumbnail_url"])

    def test_cannot_read_another_users_video(self):
        video = self.make_video(owner=self.other)

        self.assertEqual(
            self.client.get(self.detail_url(video)).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_requires_authentication(self):
        video = self.make_video()
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.detail_url(video)).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_status_filter_does_not_hide_a_direct_lookup(self):
        video = self.make_video(Status.READY)

        response = self.client.get(self.detail_url(video), {"status": "FAILED"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_shows_why_it_failed(self):
        video = self.make_video(Status.FAILED, error_message="Bad file.")

        self.assertEqual(self.client.get(self.detail_url(video)).data["error_message"], "Bad file.")

    def test_shows_live_progress(self):
        video = self.make_video(Status.PROCESSING, progress=40)
        reporter = ProgressReporter(video)
        reporter.progress = 40
        reporter.report(43)

        self.assertEqual(self.client.get(self.detail_url(video)).data["progress"], 43)

    def test_playback_url_keeps_the_public_host(self):
        video = self.make_video()

        response = self.client.get(self.detail_url(video), HTTP_X_FORWARDED_HOST="localhost:8080")

        self.assertTrue(response.data["playback_url"].startswith("http://localhost:8080/api/"))


class UpdateVideoTests(VideoApiTestCase):
    def test_updates_title_and_description(self):
        video = self.make_video()

        response = self.client.patch(
            self.detail_url(video), {"title": "New", "description": "Text"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "New")
        self.assertIn("playback_url", response.data)
        video.refresh_from_db()
        self.assertEqual((video.title, video.description), ("New", "Text"))

    def test_can_update_one_field(self):
        video = self.make_video(description="Keep me")

        self.client.patch(self.detail_url(video), {"title": "New"}, format="json")

        video.refresh_from_db()
        self.assertEqual((video.title, video.description), ("New", "Keep me"))

    def test_ignores_protected_fields(self):
        video = self.make_video(Status.PROCESSING)

        self.client.patch(
            self.detail_url(video),
            {
                "title": "New",
                "status": "READY",
                "progress": 100,
                "owner": self.other.pk,
                "hls_master_key": "videos/other/hls/master.m3u8",
            },
            format="json",
        )

        video.refresh_from_db()
        self.assertEqual(video.status, Status.PROCESSING)
        self.assertEqual(video.progress, 0)
        self.assertEqual(video.owner, self.owner)
        self.assertEqual(video.hls_master_key, "")

    def test_validates_input(self):
        video = self.make_video()

        for payload in ({"title": ""}, {"title": "x" * 256}, {"description": "x" * 5001}):
            with self.subTest(next(iter(payload))):
                response = self.client.patch(self.detail_url(video), payload, format="json")

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_another_users_video(self):
        video = self.make_video(owner=self.other)

        response = self.client.patch(self.detail_url(video), {"title": "Hacked"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        video.refresh_from_db()
        self.assertEqual(video.title, "Demo")

    def test_does_not_overwrite_what_the_worker_just_saved(self):
        video = self.make_video(Status.PROCESSING, progress=10)
        original = VideoUpdateSerializer.update

        def worker_writes_first(serializer, instance, validated_data):
            Video.objects.filter(pk=video.pk).update(status=Status.READY, progress=100)
            return original(serializer, instance, validated_data)

        with mock.patch.object(VideoUpdateSerializer, "update", worker_writes_first):
            self.client.patch(self.detail_url(video), {"title": "New"}, format="json")

        video.refresh_from_db()
        self.assertEqual(video.title, "New")
        self.assertEqual((video.status, video.progress), (Status.READY, 100))

    def test_put_is_not_allowed(self):
        video = self.make_video()

        response = self.client.put(self.detail_url(video), {"title": "New"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class DeleteVideoTests(VideoApiTestCase):
    def test_deletes_the_row_and_queues_file_removal(self):
        video = self.make_video(Status.PENDING_UPLOAD, upload_id="upload-1")
        video_id = str(video.id)

        response = self.client.delete(self.detail_url(video))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Video.objects.filter(pk=video_id).exists())
        self.delete_files.delay.assert_called_once_with(video_id, upload_id="upload-1")

    def test_cannot_delete_another_users_video(self):
        video = self.make_video(owner=self.other)

        response = self.client.delete(self.detail_url(video))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Video.objects.filter(pk=video.pk).exists())
        self.delete_files.delay.assert_not_called()

    def test_keeps_the_row_if_cleanup_cannot_be_queued(self):
        video = self.make_video()
        self.delete_files.delay.side_effect = OperationalError("broker down")

        with self.assertLogs("videos.views", level="ERROR"):
            response = self.client.delete(self.detail_url(video))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertTrue(Video.objects.filter(pk=video.pk).exists())

    def test_deleting_twice_returns_404(self):
        video = self.make_video()
        url = self.detail_url(video)
        self.client.delete(url)

        self.assertEqual(self.client.delete(url).status_code, status.HTTP_404_NOT_FOUND)


class DeleteVideoFilesTaskTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch("videos.tasks.s3_client")
        self.s3 = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.video_id = str(uuid.uuid4())
        self.keys = [f"videos/{self.video_id}/source", f"videos/{self.video_id}/hls/master.m3u8"]
        self.s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": key} for key in self.keys]}
        ]
        self.s3.delete_objects.return_value = {}

    def test_removes_everything_under_the_video_prefix(self):
        delete_video_files(self.video_id)

        prefix = self.s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"]
        self.assertEqual(prefix, f"videos/{self.video_id}/")
        deleted = self.s3.delete_objects.call_args.kwargs["Delete"]["Objects"]
        self.assertCountEqual([item["Key"] for item in deleted], self.keys)
        self.s3.abort_multipart_upload.assert_not_called()

    def test_aborts_an_unfinished_upload(self):
        delete_video_files(self.video_id, upload_id="upload-1")

        kwargs = self.s3.abort_multipart_upload.call_args.kwargs
        self.assertEqual(kwargs["UploadId"], "upload-1")
        self.assertEqual(kwargs["Key"], f"videos/{self.video_id}/source")

    def test_tolerates_an_upload_already_gone(self):
        self.s3.abort_multipart_upload.side_effect = s3_error("NoSuchUpload")

        delete_video_files(self.video_id, upload_id="upload-1")

        self.s3.delete_objects.assert_called_once()

    def test_refuses_an_id_that_is_not_a_uuid(self):
        with self.assertRaises(ValueError):
            delete_video_files("../other-user")

        self.s3.get_paginator.assert_not_called()

    def test_storage_failure_is_raised_for_celery_to_retry(self):
        self.s3.get_paginator.side_effect = STORAGE_DOWN

        with self.assertRaises(EndpointConnectionError):
            delete_video_files(self.video_id)


class PlaybackTokenTests(SimpleTestCase):
    def setUp(self):
        self.video = Video(id=uuid.uuid4())

    def test_round_trip(self):
        token = make_playback_token(self.video)

        self.assertEqual(read_playback_token(token), str(self.video.id))

    def test_rejects_tampered_tokens(self):
        token = make_playback_token(self.video)
        other = str(uuid.uuid4())

        self.assertIsNone(read_playback_token(token.replace(str(self.video.id), other)))
        self.assertIsNone(read_playback_token("garbage"))

    def test_rejects_tokens_signed_for_something_else(self):
        foreign = signing.TimestampSigner(salt="other", sep=".").sign(str(self.video.id))

        self.assertIsNone(read_playback_token(foreign))

    @override_settings(VIDEO_PLAYBACK_URL_EXPIRES=60)
    def test_rejects_expired_tokens(self):
        token = make_playback_token(self.video)

        with mock.patch("django.core.signing.time.time", return_value=9_999_999_999):
            self.assertIsNone(read_playback_token(token))


MASTER = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n360p/index.m3u8\n"
VARIANT = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000000,\nseg_00000.ts\n"
    "#EXTINF:1.000000,\nseg_00001.ts\n#EXT-X-ENDLIST\n"
)


@override_settings(CACHES=LOCMEM)
class PlaylistEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(email="owner@example.com")

    def setUp(self):
        private = mock.patch("videos.playback.s3_client")
        public = mock.patch("videos.playback.s3_public_client")
        self.s3 = private.start().return_value
        public.start().return_value.generate_presigned_url.side_effect = fake_presign
        self.addCleanup(private.stop)
        self.addCleanup(public.stop)
        self.video = Video(owner=self.owner, title="Demo", status=Status.READY)
        self.video.hls_master_key = f"videos/{self.video.id}/hls/master.m3u8"
        self.video.save()
        self.token = make_playback_token(self.video)

    def stub_playlist(self, text):
        self.s3.get_object.return_value = {"Body": mock.Mock(read=lambda: text.encode())}

    def master_url(self, token=None):
        return reverse("video-playlist-master", args=[token or self.token])

    def rendition_url(self, rendition="360p"):
        return reverse("video-playlist-rendition", args=[self.token, rendition])

    def test_serves_the_master_without_login(self):
        self.stub_playlist(MASTER)

        response = self.client.get(self.master_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.apple.mpegurl")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.content.decode(), MASTER)

    def test_signs_every_segment(self):
        self.stub_playlist(VARIANT)

        body = self.client.get(self.rendition_url()).content.decode()

        folder = f"https://s3.test/videos/{self.video.id}/hls/360p"
        self.assertIn(f"{folder}/seg_00000.ts?signature=abc", body)
        self.assertIn(f"{folder}/seg_00001.ts?signature=abc", body)
        self.assertNotIn("\nseg_", body)
        self.assertIn("#EXT-X-ENDLIST", body)
        self.assertEqual(
            self.s3.get_object.call_args.kwargs["Key"],
            f"videos/{self.video.id}/hls/360p/index.m3u8",
        )

    def test_rejects_an_invalid_token(self):
        self.assertEqual(self.client.get(self.master_url("forged.token.x")).status_code, 403)
        self.s3.get_object.assert_not_called()

    def test_rejects_rendition_names_that_are_not_renditions(self):
        for rendition in ("..", "admin", "360", "360p.."):
            with self.subTest(rendition):
                self.assertEqual(self.client.get(self.rendition_url(rendition)).status_code, 404)
        self.s3.get_object.assert_not_called()

    def test_video_that_is_not_ready_has_no_playlist(self):
        Video.objects.filter(pk=self.video.pk).update(status=Status.PROCESSING)

        self.assertEqual(self.client.get(self.master_url()).status_code, 404)

    def test_deleted_video_stops_playing(self):
        Video.objects.filter(pk=self.video.pk).delete()

        self.assertEqual(self.client.get(self.master_url()).status_code, 404)

    def test_missing_playlist_is_a_404(self):
        self.s3.get_object.side_effect = s3_error("NoSuchKey")

        self.assertEqual(self.client.get(self.rendition_url("720p")).status_code, 404)

    def test_storage_failure_is_a_503(self):
        self.s3.get_object.side_effect = STORAGE_DOWN

        with self.assertLogs("videos.views", level="ERROR"):
            response = self.client.get(self.master_url())

        self.assertEqual(response.status_code, 503)

    def test_only_reads_are_allowed(self):
        self.assertEqual(self.client.post(self.master_url()).status_code, 405)


class ApiDocsTests(TestCase):
    def test_schema_describes_the_video_api(self):
        response = self.client.get(reverse("schema"), {"format": "json"})

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        for path in ("/api/videos/", "/api/videos/{id}/", "/api/videos/{id}/status/"):
            self.assertIn(path, paths)

    def test_docs_page_loads(self):
        self.assertEqual(self.client.get(reverse("docs")).status_code, 200)
