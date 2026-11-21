from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

User = get_user_model()
DUPLICATE_EMAIL = User._meta.get_field("email").error_messages["unique"]


class NormalizedEmailField(serializers.EmailField):
    """Normalizes first, so length and uniqueness checks apply to the stored value."""

    def to_internal_value(self, data):
        return User.objects.normalize_email(super().to_internal_value(data))


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "date_joined"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    email = NormalizedEmailField(
        max_length=User._meta.get_field("email").max_length,
        validators=[UniqueValidator(queryset=User.objects.all(), message=DUPLICATE_EMAIL)],
    )
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["id", "email", "password", "date_joined"]
        read_only_fields = ["id", "date_joined"]

    def validate(self, attrs):
        candidate = User(email=attrs["email"])
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return User.objects.create_user(**validated_data)
        except IntegrityError as exc:
            # Lost a race against a concurrent signup with the same email.
            raise serializers.ValidationError({"email": [DUPLICATE_EMAIL]}) from exc
