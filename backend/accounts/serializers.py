from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()
EMAIL_MAX_LENGTH = User._meta.get_field("email").max_length
TOKEN_MAX_LENGTH = 512


class NormalizedEmailField(serializers.EmailField):
    """Normalizes first, so length and uniqueness checks apply to the stored value."""

    def to_internal_value(self, data):
        return User.objects.normalize_email(super().to_internal_value(data))


class UserSerializer(serializers.ModelSerializer):
    email_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "email_verified", "date_joined"]
        read_only_fields = fields


class AccessTokenSerializer(serializers.Serializer):
    # The refresh token never travels in a body: it lives in an httpOnly cookie.
    access = serializers.CharField(read_only=True)


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class RegisterSerializer(serializers.Serializer):
    # No uniqueness check here: the answer must not reveal which emails exist.
    email = NormalizedEmailField(max_length=EMAIL_MAX_LENGTH)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)

    def validate(self, attrs):
        candidate = User(email=attrs["email"])
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs


class EmailVerifySerializer(serializers.Serializer):
    token = serializers.CharField(max_length=TOKEN_MAX_LENGTH)


class PasswordResetSerializer(serializers.Serializer):
    email = NormalizedEmailField(max_length=EMAIL_MAX_LENGTH)


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=TOKEN_MAX_LENGTH)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
