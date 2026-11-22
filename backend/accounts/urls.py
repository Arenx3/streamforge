from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import (
    EmailResendView,
    EmailVerifyView,
    LoginView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetView,
    RegisterView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("token/", LoginView.as_view(), name="auth-token"),
    path("token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("me/", MeView.as_view(), name="auth-me"),
    path("email/verify/", EmailVerifyView.as_view(), name="auth-email-verify"),
    path("email/resend/", EmailResendView.as_view(), name="auth-email-resend"),
    path("password/reset/", PasswordResetView.as_view(), name="auth-password-reset"),
    path(
        "password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
]
