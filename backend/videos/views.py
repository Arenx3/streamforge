import logging
from contextlib import contextmanager

from botocore.exceptions import BotoCoreError, ClientError
from kombu.exceptions import OperationalError as BrokerError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from videos.models import InvalidTransition, Video
from videos.progress import live_progress
from videos.serializers import VideoCreateSerializer, VideoSerializer
from videos.tasks import transcode_video
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


class ProcessingUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Processing could not be started. Try again."
    default_code = "processing_unavailable"


@contextmanager
def storage_errors_as_503():
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        logger.exception("S3 request failed")
        raise StorageUnavailable from exc


def enqueue_transcode(video):
    try:
        transcode_video.delay(str(video.id))
    except BrokerError as exc:
        logger.exception("Could not enqueue processing of video %s", video.pk)
        raise ProcessingUnavailable from exc


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
        # Also on retries: the task ignores duplicates, and a failed enqueue recovers.
        if video.status == Video.Status.UPLOADED:
            enqueue_transcode(video)
        return Response(VideoSerializer(video).data)

    @action(detail=True, methods=["get"], url_path="status", url_name="status")
    def processing_status(self, request, pk=None):
        video = self.get_object()
        return Response(
            {
                "status": video.status,
                "progress": live_progress(video),
                "error_message": video.error_message,
            }
        )
