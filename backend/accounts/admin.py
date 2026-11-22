from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.utils import timezone

from accounts.models import User


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ["email"]


class UserChangeForm(BaseUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "is_staff", "is_active", "date_joined"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Personal info", {"fields": ["first_name", "last_name"]}),
        (
            "Permissions",
            {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]},
        ),
        ("Important dates", {"fields": ["last_login", "date_joined", "email_verified_at"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "usable_password", "password1", "password2"],
            },
        ),
    ]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.email_verified_at = timezone.now()  # Staff vouch for who they add.
        super().save_model(request, obj, form, change)
