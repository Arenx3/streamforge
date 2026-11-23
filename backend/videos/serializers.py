from django.conf import settings
from rest_framework import serializers

from core.storage import s3_public_client
from videos.models import Video
from videos.playback import playback_url, presigned_get
from videos.progress import live_progress


class VideoSerializer(serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            "id",
            "title",
            "description",
            "status",
            "progress",
            "thumbnail_url",
            "duration_seconds",
            "width",
            "height",
            "size_bytes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_progress(self, video) -> int:
        return live_progress(video)

    def get_thumbnail_url(self, video) -> str | None:
        if not video.thumbnail_key:
            return None
        # Building a client is slow, so a whole page of results shares one.
        if "public_s3" not in self.context:
            self.context["public_s3"] = s3_public_client()
        return presigned_get(self.context["public_s3"], video.thumbnail_key)


class VideoDetailSerializer(VideoSerializer):
    playback_url = serializers.SerializerMethodField()

    class Meta(VideoSerializer.Meta):
        fields = [*VideoSerializer.Meta.fields, "error_message", "processed_at", "playback_url"]
        read_only_fields = fields

    def get_playback_url(self, video) -> str | None:
        return playback_url(self.context["request"], video)


class VideoUpdateSerializer(serializers.ModelSerializer):
    description = serializers.CharField(required=False, allow_blank=True, max_length=5000)

    class Meta:
        model = Video
        fields = ["title", "description"]

    def update(self, video, validated_data):
        for field, value in validated_data.items():
            setattr(video, field, value)
        # Only these columns: a full save would overwrite what the worker is writing.
        video.save(update_fields=[*validated_data, "updated_at"])
        return video


class VideoCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=Video._meta.get_field("title").max_length)
    description = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=5000
    )
    content_type = serializers.RegexField(r"^video/[a-z0-9][a-z0-9.+-]*\Z", max_length=127)
    size_bytes = serializers.IntegerField(min_value=1)

    def validate_size_bytes(self, value):
        limit = settings.VIDEO_UPLOAD_MAX_BYTES
        if value > limit:
            raise serializers.ValidationError(f"The file exceeds the {limit} byte limit.")
        return value


class UploadPartSerializer(serializers.Serializer):
    part_number = serializers.IntegerField()
    size = serializers.IntegerField()
    url = serializers.URLField()


class UploadTicketSerializer(serializers.Serializer):
    part_size = serializers.IntegerField()
    expires_in = serializers.IntegerField()
    parts = UploadPartSerializer(many=True)


class VideoCreatedSerializer(serializers.Serializer):
    video = VideoSerializer()
    upload = UploadTicketSerializer()


class VideoSummarySerializer(serializers.Serializer):
    PENDING_UPLOAD = serializers.IntegerField()
    UPLOADED = serializers.IntegerField()
    PROCESSING = serializers.IntegerField()
    READY = serializers.IntegerField()
    FAILED = serializers.IntegerField()


class VideoStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Video.Status.choices)
    progress = serializers.IntegerField()
    error_message = serializers.CharField(allow_blank=True)
