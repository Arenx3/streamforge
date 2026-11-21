from rest_framework.routers import SimpleRouter

from videos.views import VideoViewSet

router = SimpleRouter()
router.register("videos", VideoViewSet, basename="video")

urlpatterns = router.urls
