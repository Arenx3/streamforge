import json
import time

from django.db import connection
from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from videos.events import event_stream

FALLBACK_STREAM_SECONDS = 900


class EventStreamRenderer(BaseRenderer):
    """Lets clients ask for text/event-stream. Only error responses go through it."""

    media_type = "text/event-stream"
    format = "sse"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data).encode()


class VideoEventsView(APIView):
    renderer_classes = [EventStreamRenderer]

    @extend_schema(
        summary="Stream live updates of your videos",
        description="Server-Sent Events. Ends when the access token expires.",
        responses={(200, "text/event-stream"): OpenApiTypes.STR},
    )
    def get(self, request):
        expires_at = request.auth.get("exp") if request.auth else None
        # The stream can last minutes and must not hold a database connection meanwhile.
        if not connection.in_atomic_block:
            connection.close()
        response = StreamingHttpResponse(
            event_stream(request.user.pk, expires_at or time.time() + FALLBACK_STREAM_SECONDS),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-store"
        response["X-Accel-Buffering"] = "no"
        return response
