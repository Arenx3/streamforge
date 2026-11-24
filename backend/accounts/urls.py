from django.urls import path

from accounts.views import (
    DemoLoginView,
    EmailResendView,
    EmailVerifyView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetView,
    RefreshView,
    RegisterView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("token/", LoginView.as_view(), name="auth-token"),
    path("token/refresh/", RefreshView.as_view(), name="auth-token-refresh"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("demo/", DemoLoginView.as_view(), name="auth-demo"),
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
