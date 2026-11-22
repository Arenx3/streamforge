from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import (
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
