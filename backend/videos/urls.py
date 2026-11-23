from django.urls import path
from rest_framework.routers import SimpleRouter

from videos.streams import VideoEventsView
from videos.views import VideoViewSet, playlist

router = SimpleRouter()
router.register("videos", VideoViewSet, basename="video")

urlpatterns = [
    path("videos/events/", VideoEventsView.as_view(), name="video-events"),
    path("playback/<str:token>/master.m3u8", playlist, name="video-playlist-master"),
    path(
        "playback/<str:token>/<str:rendition>/index.m3u8",
        playlist,
        name="video-playlist-rendition",
    ),
    *router.urls,
]
