import logging
from contextlib import contextmanager

from botocore.exceptions import BotoCoreError, ClientError
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.views.decorators.http import require_safe
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from kombu.exceptions import OperationalError as BrokerError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from accounts.permissions import IsEmailVerified
from core.storage import STORAGE_ERRORS
from videos.models import InvalidTransition, Video
from videos.playback import (
    PlaylistNotFound,
    master_playlist,
    read_playback_token,
    rendition_playlist,
)
from videos.progress import live_progress
from videos.serializers import (
    VideoCreatedSerializer,
    VideoCreateSerializer,
    VideoDetailSerializer,
    VideoSerializer,
    VideoStatusSerializer,
    VideoSummarySerializer,
    VideoUpdateSerializer,
)
from videos.tasks import delete_video_files, transcode_video
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


class VideoPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@require_safe
def playlist(request, token, rendition=None):
    """Serve an HLS playlist to whoever holds a valid playback token."""
    video_id = read_playback_token(token)
    if video_id is None:
        return HttpResponse(status=403)
    video = Video.objects.filter(pk=video_id, status=Video.Status.READY).first()
    if video is None:
        return HttpResponse(status=404)
    try:
        body = rendition_playlist(video, rendition) if rendition else master_playlist(video)
    except PlaylistNotFound:
        return HttpResponse(status=404)
    except STORAGE_ERRORS:
        logger.exception("Could not read a playlist of video %s", video.pk)
        return HttpResponse(status=503)
    response = HttpResponse(body, content_type="application/vnd.apple.mpegurl")
    response["Cache-Control"] = "no-store"  # It carries presigned URLs.
    return response


@extend_schema_view(
    list=extend_schema(
        summary="List your videos",
        parameters=[
            OpenApiParameter(
                "status", str, description="Only videos in these states, separated by commas"
            )
        ],
    ),
    create=extend_schema(
        summary="Start an upload",
        description="Creates the video and returns one presigned URL per part.",
        request=VideoCreateSerializer,
        responses={201: VideoCreatedSerializer},
    ),
    retrieve=extend_schema(summary="Get a video with its playback URL"),
    partial_update=extend_schema(
        summary="Edit title or description",
        request=VideoUpdateSerializer,
        responses=VideoDetailSerializer,
    ),
    destroy=extend_schema(
        summary="Delete a video and its files",
        responses={204: None},
    ),
    complete=extend_schema(
        summary="Finish an upload and start processing",
        request=None,
        responses=VideoSerializer,
    ),
    summary=extend_schema(
        summary="Count your videos by state",
        responses=VideoSummarySerializer,
    ),
    processing_status=extend_schema(
        summary="Get live processing progress",
        responses=VideoStatusSerializer,
    ),
)
class VideoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    pagination_class = VideoPagination
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Video.objects.none()
        return Video.objects.filter(owner=self.request.user).order_by("-created_at", "-id")

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.action == "create":
            permissions.append(IsEmailVerified())
        return permissions

    def get_serializer_class(self):
        return VideoSerializer if self.action == "list" else VideoDetailSerializer

    def filter_queryset(self, queryset):
        wanted = self.request.query_params.get("status")
        if self.action != "list" or wanted is None:
            return queryset
        statuses = wanted.split(",")
        if not set(statuses) <= set(Video.Status.values):
            raise ValidationError({"status": [f"Must be one of: {', '.join(Video.Status.values)}"]})
        return queryset.filter(status__in=statuses)

    def partial_update(self, request, pk=None):
        video = self.get_object()
        serializer = VideoUpdateSerializer(video, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.get_serializer(video).data)

    def destroy(self, request, pk=None):
        video = self.get_object()
        video_id, upload_id = str(video.pk), video.upload_id
        try:
            # The row only goes away once its file cleanup is safely queued.
            with transaction.atomic():
                video.delete()
                delete_video_files.delay(video_id, upload_id=upload_id)
        except BrokerError as exc:
            logger.exception("Could not queue the deletion of video %s", video_id)
            raise ProcessingUnavailable("The video could not be deleted. Try again.") from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

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

    @action(detail=False, methods=["get"])
    def summary(self, request):
        rows = self.get_queryset().order_by().values_list("status").annotate(Count("pk"))
        counts = dict(rows)
        return Response({status: counts.get(status, 0) for status in Video.Status.values})

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
