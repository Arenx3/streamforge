from django.contrib import admin

from videos.models import Video


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "status", "progress", "size_bytes", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["id", "title", "owner__email"]
    list_select_related = ["owner"]
    date_hierarchy = "created_at"
    # Status and artifacts are driven by the processing pipeline, not edited by hand.
    readonly_fields = [
        "id",
        "status",
        "progress",
        "error_message",
        "source_key",
        "hls_master_key",
        "thumbnail_key",
        "duration_seconds",
        "width",
        "height",
        "size_bytes",
        "created_at",
        "updated_at",
        "processed_at",
    ]
    fieldsets = [
        (None, {"fields": ["id", "owner", "title", "description"]}),
        ("Processing", {"fields": ["status", "progress", "error_message", "processed_at"]}),
        ("Storage", {"fields": ["source_key", "hls_master_key", "thumbnail_key"]}),
        ("Metadata", {"fields": ["duration_seconds", "width", "height", "size_bytes"]}),
        ("Timestamps", {"fields": ["created_at", "updated_at"]}),
    ]
