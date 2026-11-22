from rest_framework import permissions, status
from rest_framework.exceptions import APIException


class EmailNotVerified(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = {
        "detail": "Verify your email before uploading videos.",
        "code": "email_not_verified",
    }


class IsEmailVerified(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated and not request.user.email_verified:
            raise EmailNotVerified
        return True
