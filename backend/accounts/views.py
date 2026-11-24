from contextlib import suppress

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.demo import ensure_demo_video, get_demo_user
from accounts.serializers import (
    AccessTokenSerializer,
    DetailSerializer,
    EmailVerifySerializer,
    PasswordResetConfirmSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserSerializer,
)
from accounts.services import (
    confirm_email,
    register_account,
    request_password_reset,
    resend_verification,
    reset_password,
)
from accounts.session import forget_refresh_cookie, keep_refresh_in_cookie, refresh_from_cookie

CHECK_INBOX = "If this email can be used, you will receive a message with the next steps."


class PublicAuthView(generics.GenericAPIView):
    # Public endpoints: a stale Authorization header must not turn into a 401.
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]


class RegisterView(PublicAuthView):
    serializer_class = RegisterSerializer
    throttle_scope = "auth_register"

    @extend_schema(summary="Create an account", responses={202: DetailSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        register_account(**serializer.validated_data)
        return Response({"detail": CHECK_INBOX}, status=status.HTTP_202_ACCEPTED)


class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        summary="Log in with email and password",
        description="The refresh token is set as an httpOnly cookie.",
        responses=AccessTokenSerializer,
    )
    def post(self, request, *args, **kwargs):
        return keep_refresh_in_cookie(super().post(request, *args, **kwargs))


class RefreshView(TokenRefreshView):
    @extend_schema(
        summary="Renew the access token",
        description="Reads the refresh token from its cookie and rotates it.",
        request=None,
        responses=AccessTokenSerializer,
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data={"refresh": refresh_from_cookie(request)})
        try:
            serializer.is_valid(raise_exception=True)
        except (TokenError, ValidationError):
            # A dead cookie is dropped, so the browser stops sending it.
            return forget_refresh_cookie(self.handle_exception(InvalidToken()))
        return keep_refresh_in_cookie(Response(serializer.validated_data))


class LogoutView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="End the session", request=None, responses={204: None})
    def post(self, request):
        token = refresh_from_cookie(request)
        if token:
            # An expired or forged token has nothing left to revoke.
            with suppress(TokenError):
                RefreshToken(token).blacklist()
        return forget_refresh_cookie(Response(status=status.HTTP_204_NO_CONTENT))


class DemoLoginView(PublicAuthView):
    serializer_class = AccessTokenSerializer
    throttle_scope = "auth_demo"

    @extend_schema(summary="Enter the local demo account", request=None)
    def post(self, request):
        if not settings.DEMO_ENABLED:
            raise NotFound
        user = get_demo_user()
        ensure_demo_video(user)
        refresh = RefreshToken.for_user(user)
        return keep_refresh_in_cookie(
            Response({"access": str(refresh.access_token), "refresh": str(refresh)})
        )


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class EmailVerifyView(generics.GenericAPIView):
    serializer_class = EmailVerifySerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email_verify"

    @extend_schema(summary="Confirm your email with the emailed token", responses=UserSerializer)
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirm_email(request.user, serializer.validated_data["token"])
        return Response(UserSerializer(request.user).data)


class EmailResendView(generics.GenericAPIView):
    serializer_class = DetailSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_email_resend"

    @extend_schema(
        summary="Send the verification email again",
        request=None,
        responses={202: DetailSerializer},
    )
    def post(self, request):
        resend_verification(request.user)
        return Response({"detail": CHECK_INBOX}, status=status.HTTP_202_ACCEPTED)


class PasswordResetView(PublicAuthView):
    serializer_class = PasswordResetSerializer
    throttle_scope = "auth_password_reset"

    @extend_schema(summary="Request a password reset", responses={202: DetailSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_password_reset(serializer.validated_data["email"])
        return Response({"detail": CHECK_INBOX}, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(PublicAuthView):
    serializer_class = PasswordResetConfirmSerializer
    throttle_scope = "auth_password_confirm"

    @extend_schema(
        summary="Set a new password and close every session",
        responses={204: None},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_password(**serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)
