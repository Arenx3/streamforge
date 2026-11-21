import logging
from contextlib import contextmanager

from botocore.exceptions import BotoCoreError, ClientError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from videos.models import InvalidTransition, Video
from videos.serializers import VideoCreateSerializer, VideoSerializer
from videos.uploads import UploadIncomplete, finish_upload, start_upload

logger = logging.getLogger(__name__)


class UploadConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The upload cannot be completed."
    default_code = "upload_conflict"


class StorageUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Storage is temporarily unavailable. Try again."
    default_code = "storage_unavailable"


@contextmanager
def storage_errors_as_503():
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        logger.exception("S3 request failed")
        raise StorageUnavailable from exc


class VideoViewSet(viewsets.GenericViewSet):
    serializer_class = VideoSerializer

    def get_queryset(self):
        return Video.objects.filter(owner=self.request.user)

    def create(self, request):
        serializer = VideoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with storage_errors_as_503():
            video, upload = start_upload(owner=request.user, **serializer.validated_data)
        return Response(
            {"video": VideoSerializer(video).data, "upload": upload},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        video = self.get_object()
        if video.status == Video.Status.PENDING_UPLOAD:
            try:
                with storage_errors_as_503():
                    finish_upload(video)
            except UploadIncomplete as exc:
                raise UploadConflict(str(exc)) from exc
            except InvalidTransition:
                # A concurrent request finished it first.
                video.refresh_from_db()
        if video.status == Video.Status.FAILED:
            raise UploadConflict("This upload failed or was abandoned.")
        return Response(VideoSerializer(video).data)
