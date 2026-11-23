from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from videos.models import Video
from videos.playback import make_playback_token, read_share_playback_token
from videos.test_api import LOCMEM, MASTER, VARIANT, fake_presign
from videos.views import PublicVideoThrottle

Status = Video.Status


def tight_limit():
    return mock.patch.dict(PublicVideoThrottle.THROTTLE_RATES, {"video_share": "2/minute"})


def make_video(owner, video_status=Status.READY, **extra):
    video = Video(owner=owner, title="Demo", status=video_status, **extra)
    if video_status == Status.READY:
        video.hls_master_key = f"videos/{video.id}/hls/master.m3u8"
        video.thumbnail_key = f"videos/{video.id}/thumbnail.webp"
    video.save()
    return video


class SharingTestCase:
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="owner@example.com", email_verified_at=timezone.now())
        cls.other = users.create_user(email="other@example.com", email_verified_at=timezone.now())

    def setUp(self):
        cache.clear()
        for target in ("videos.serializers.s3_public_client", "videos.playback.s3_public_client"):
            patcher = mock.patch(target)
            patcher.start().return_value.generate_presigned_url.side_effect = fake_presign
            self.addCleanup(patcher.stop)


@override_settings(CACHES=LOCMEM)
class ShareActionTests(SharingTestCase, APITestCase):
    def setUp(self):
        super().setUp()
        self.video = make_video(self.owner)
        self.url = reverse("video-share", args=[self.video.pk])
        self.client.force_authenticate(self.owner)

    def test_creates_an_unguessable_link(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.video.refresh_from_db()
        self.assertEqual(response.data["share_token"], self.video.share_token)
        self.assertGreaterEqual(len(self.video.share_token), 32)

    def test_creating_twice_keeps_the_same_link(self):
        first = self.client.post(self.url).data["share_token"]

        self.assertEqual(self.client.post(self.url).data["share_token"], first)

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_a_verified_email(self):
        unverified = get_user_model().objects.create_user(email="new@example.com")
        video = make_video(unverified)
        self.client.force_authenticate(unverified)

        response = self.client.post(reverse("video-share", args=[video.pk]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        video.refresh_from_db()
        self.assertIsNone(video.share_token)

    def test_is_invisible_to_other_users(self):
        self.video.enable_sharing()
        token = self.video.share_token
        self.client.force_authenticate(self.other)

        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(self.url).status_code, status.HTTP_404_NOT_FOUND)
        self.video.refresh_from_db()
        self.assertEqual(self.video.share_token, token)

    def test_only_a_ready_video_can_be_shared(self):
        for video_status in set(Status) - {Status.READY}:
            with self.subTest(video_status):
                video = make_video(self.owner, video_status)

                response = self.client.post(reverse("video-share", args=[video.pk]))

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                video.refresh_from_db()
                self.assertIsNone(video.share_token)

    def test_turning_it_off_clears_the_link(self):
        self.client.post(self.url)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.video.refresh_from_db()
        self.assertIsNone(self.video.share_token)

    def test_turning_it_back_on_makes_a_new_link(self):
        first = self.client.post(self.url).data["share_token"]
        self.client.delete(self.url)

        self.assertNotEqual(self.client.post(self.url).data["share_token"], first)

    def test_the_list_never_carries_the_link(self):
        self.client.post(self.url)

        row = self.client.get(reverse("video-list")).data["results"][0]

        self.assertNotIn("share_token", row)

    def test_the_owner_sees_the_link_in_the_detail(self):
        token = self.client.post(self.url).data["share_token"]

        detail = self.client.get(reverse("video-detail", args=[self.video.pk]))

        self.assertEqual(detail.data["share_token"], token)


@override_settings(CACHES=LOCMEM, VIDEO_SHARE_URL_EXPIRES=900)
class PublicVideoTests(SharingTestCase, APITestCase):
    def setUp(self):
        super().setUp()
        self.video = make_video(self.owner, description="Para todos")
        self.video.enable_sharing()
        self.url = reverse("shared-video", args=[self.video.share_token])

    def test_opens_without_an_account(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Demo")
        self.assertEqual(response.data["description"], "Para todos")
        self.assertIsNotNone(response.data["playback_url"])

    def test_only_exposes_what_a_visitor_needs(self):
        response = self.client.get(self.url)

        self.assertEqual(
            set(response.data),
            {
                "title",
                "description",
                "duration_seconds",
                "width",
                "height",
                "thumbnail_url",
                "playback_url",
            },
        )
        self.assertNotIn("owner@example.com", response.content.decode())
        self.assertNotIn(str(self.video.id), response.data["playback_url"])

    def test_is_not_cached_or_indexed(self):
        response = self.client.get(self.url)

        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_ignores_a_stale_session(self):
        response = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer expired")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_rejects_unknown_links(self):
        response = self.client.get(reverse("shared-video", args=["not-a-real-link"]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_revoked_link_stops_at_once(self):
        self.video.disable_sharing()

        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_a_deactivated_account_takes_its_links_down(self):
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)

        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_is_read_only(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_thumbnail_uses_the_short_lifetime(self):
        with mock.patch("videos.serializers.presigned_get", return_value="url") as presigned:
            self.client.get(self.url)

        self.assertEqual(presigned.call_args.args[2], 900)

    def test_is_rate_limited(self):
        with tight_limit():
            codes = [self.client.get(self.url).status_code for _ in range(3)]

        self.assertEqual(codes, [200, 200, status.HTTP_429_TOO_MANY_REQUESTS])


@override_settings(CACHES=LOCMEM, VIDEO_SHARE_URL_EXPIRES=900)
class SharedPlaylistTests(SharingTestCase, TestCase):
    def setUp(self):
        super().setUp()
        private = mock.patch("videos.playback.s3_client")
        self.s3 = private.start().return_value
        self.addCleanup(private.stop)
        self.video = make_video(self.owner)
        self.video.enable_sharing()
        page = self.client.get(reverse("shared-video", args=[self.video.share_token]))
        self.master = page.json()["playback_url"]
        self.token = self.master.split("/")[-2]

    def stub_playlist(self, text):
        self.s3.get_object.return_value = {"Body": mock.Mock(read=lambda: text.encode())}

    def rendition_url(self, rendition="360p"):
        return reverse("shared-playlist-rendition", args=[self.token, rendition])

    def test_serves_the_master_without_login(self):
        self.stub_playlist(MASTER)

        response = self.client.get(self.master)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.apple.mpegurl")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response.content.decode(), MASTER)

    def test_signs_segments_with_the_short_lifetime(self):
        self.stub_playlist(VARIANT)
        signer = mock.Mock()
        signer.generate_presigned_url.side_effect = fake_presign

        with mock.patch("videos.playback.s3_public_client", return_value=signer):
            body = self.client.get(self.rendition_url()).content.decode()

        self.assertIn("seg_00000.ts?signature=abc", body)
        self.assertNotIn("\nseg_", body)
        for call in signer.generate_presigned_url.call_args_list:
            self.assertEqual(call.kwargs["ExpiresIn"], 900)

    def test_a_revoked_link_rejects_its_playlists(self):
        self.stub_playlist(MASTER)
        self.video.disable_sharing()

        self.assertEqual(self.client.get(self.master).status_code, 403)
        self.assertEqual(self.client.get(self.rendition_url()).status_code, 403)
        self.s3.get_object.assert_not_called()

    def test_a_new_link_does_not_revive_the_old_one(self):
        self.video.disable_sharing()
        self.video.enable_sharing()

        self.assertEqual(self.client.get(self.master).status_code, 403)

    def test_a_deactivated_account_rejects_its_playlists(self):
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)

        self.assertEqual(self.client.get(self.master).status_code, 403)

    def test_rejects_a_forged_token(self):
        url = reverse("shared-playlist-master", args=["forged.token.x"])

        self.assertEqual(self.client.get(url).status_code, 403)

    def test_rejects_a_token_signed_for_something_else(self):
        foreign = signing.TimestampSigner(salt="other", sep=".").sign(self.video.share_token)

        self.assertIsNone(read_share_playback_token(foreign))

    def test_an_expired_token_is_rejected(self):
        with mock.patch("django.core.signing.time.time", return_value=9_999_999_999):
            self.assertIsNone(read_share_playback_token(self.token))

    def test_a_private_token_does_not_open_the_public_route(self):
        private = make_playback_token(self.video)
        url = reverse("shared-playlist-master", args=[private])

        self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_public_token_does_not_open_the_private_route(self):
        url = reverse("video-playlist-master", args=[self.token])

        self.assertEqual(self.client.get(url).status_code, 403)

    def test_unknown_rendition_is_not_found(self):
        response = self.client.get(self.rendition_url("secret"))

        self.assertEqual(response.status_code, 404)
        self.s3.get_object.assert_not_called()

    def test_is_rate_limited(self):
        self.stub_playlist(MASTER)
        cache.clear()

        with tight_limit():
            codes = [self.client.get(self.master).status_code for _ in range(3)]

        self.assertEqual(codes, [200, 200, 429])

    def test_is_read_only(self):
        self.assertEqual(self.client.post(self.master).status_code, 405)
