from django.conf import settings
from rest_framework import serializers

from videos.models import Video


class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = [
            "id",
            "title",
            "description",
            "status",
            "progress",
            "size_bytes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


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
