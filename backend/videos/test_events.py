import json
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from videos.events import (
    channel_for,
    event_stream,
    publish_video_deleted,
    publish_video_event,
)
from videos.models import Video
from videos.progress import ProgressReporter

Status = Video.Status
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def published(client):
    return [json.loads(call.args[1]) for call in client.publish.call_args_list]


class PublishTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(email="owner@example.com")

    def setUp(self):
        patcher = mock.patch("videos.events._client")
        self.redis = patcher.start().return_value
        self.addCleanup(patcher.stop)

    def make_video(self, video_status=Status.UPLOADED):
        return Video.objects.create(owner=self.owner, title="Demo", status=video_status)

    def test_goes_to_the_owners_channel(self):
        video = self.make_video()

        publish_video_event(video, "state")

        self.assertEqual(self.redis.publish.call_args.args[0], channel_for(self.owner.pk))
        self.assertEqual(
            published(self.redis),
            [
                {
                    "id": str(video.pk),
                    "kind": "state",
                    "status": "UPLOADED",
                    "progress": 0,
                    "error_message": "",
                }
            ],
        )

    def test_never_exposes_storage_details(self):
        video = self.make_video()
        video.source_key = "videos/private/source"

        publish_video_event(video, "state")

        self.assertNotIn("private", self.redis.publish.call_args.args[1])

    def test_redis_failure_never_interrupts_the_work(self):
        video = self.make_video()
        self.redis.publish.side_effect = RedisConnectionError("down")

        with self.assertLogs("videos.events", level="WARNING"):
            publish_video_event(video, "state")

    def test_state_changes_publish_after_commit(self):
        video = self.make_video()

        with self.captureOnCommitCallbacks(execute=True):
            video.start_processing(task_id="task-1")
            self.redis.publish.assert_not_called()

        event = published(self.redis)[-1]
        self.assertEqual((event["kind"], event["status"]), ("state", "PROCESSING"))

    def test_failure_carries_its_message(self):
        video = self.make_video(Status.PROCESSING)

        with self.captureOnCommitCallbacks(execute=True):
            video.mark_failed("Bad file.")

        self.assertEqual(published(self.redis)[-1]["error_message"], "Bad file.")

    @override_settings(CACHES=LOCMEM)
    def test_progress_is_published_with_the_reporter(self):
        cache.clear()
        video = self.make_video(Status.PROCESSING)
        clock = mock.Mock(side_effect=[0.0, 2.0])

        ProgressReporter(video, clock=clock).report(12)

        event = published(self.redis)[-1]
        self.assertEqual((event["kind"], event["progress"]), ("progress", 12))

    def test_deletion_only_carries_the_id(self):
        publish_video_deleted(self.owner.pk, "abc")

        self.assertEqual(published(self.redis), [{"id": "abc", "kind": "deleted"}])


class EventStreamTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch("videos.events.redis.Redis.from_url")
        self.client = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.pubsub = self.client.pubsub.return_value

    def test_subscribes_before_announcing_ready(self):
        self.pubsub.get_message.return_value = None
        stream = event_stream(7, time.time() + 60)

        chunks = [next(stream), next(stream)]

        self.pubsub.subscribe.assert_called_once_with("video-events:7")
        self.assertEqual(chunks[0], "retry: 3000\n\n")
        self.assertEqual(chunks[1], "event: ready\ndata: {}\n\n")
        stream.close()

    def test_relays_messages_as_events(self):
        self.pubsub.get_message.return_value = {"data": b'{"id": "abc"}'}
        stream = event_stream(7, time.time() + 60)
        next(stream), next(stream)

        self.assertEqual(next(stream), 'event: video\ndata: {"id": "abc"}\n\n')
        stream.close()

    def test_sends_keepalives_while_idle(self):
        self.pubsub.get_message.return_value = None
        with mock.patch("videos.events.time.monotonic", side_effect=[0, 20]):
            stream = event_stream(7, time.time() + 60)
            next(stream), next(stream)

            self.assertEqual(next(stream), ": keepalive\n\n")
            stream.close()

    def test_ends_when_the_token_expires(self):
        chunks = list(event_stream(7, time.time() - 1))

        self.assertEqual(chunks[-1], "event: expired\ndata: {}\n\n")
        self.pubsub.close.assert_called_once()

    def test_releases_redis_when_the_client_disconnects(self):
        self.pubsub.get_message.return_value = None
        stream = event_stream(7, time.time() + 60)
        next(stream)

        stream.close()

        self.pubsub.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_redis_failure_ends_the_stream_cleanly(self):
        self.pubsub.get_message.side_effect = RedisConnectionError("down")

        with self.assertLogs("videos.events", level="WARNING"):
            chunks = list(event_stream(7, time.time() + 60))

        self.assertEqual(len(chunks), 2)
        self.pubsub.close.assert_called_once()


class VideoEventsViewTests(APITestCase):
    url = reverse("video-events")

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="owner@example.com")
        cls.other = users.create_user(email="other@example.com")

    def get(self, token=None):
        headers = {"HTTP_ACCEPT": "text/event-stream"}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.get(self.url, **headers)

    def test_requires_authentication(self):
        self.assertEqual(self.get().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_an_invalid_token(self):
        self.assertEqual(self.get("forged.token.value").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_streams_only_the_callers_channel_until_the_token_expires(self):
        token = AccessToken.for_user(self.owner)

        with mock.patch("videos.streams.event_stream", return_value=iter(["x"])) as stream:
            response = self.get(str(token))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.streaming)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        stream.assert_called_once_with(self.owner.pk, token["exp"])

    def test_owner_cannot_be_chosen_by_the_client(self):
        token = AccessToken.for_user(self.owner)

        with mock.patch("videos.streams.event_stream", return_value=iter(["x"])) as stream:
            self.client.get(
                self.url,
                {"owner": self.other.pk},
                HTTP_ACCEPT="text/event-stream",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(stream.call_args.args[0], self.owner.pk)
