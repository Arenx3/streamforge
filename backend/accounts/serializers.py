from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

User = get_user_model()
DUPLICATE_USERNAME = User._meta.get_field("username").error_messages["unique"]


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "date_joined"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "date_joined"]
        read_only_fields = ["id", "date_joined"]

    def validate_username(self, value):
        # "Alice" and "alice" must not coexist: one could impersonate the other.
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(DUPLICATE_USERNAME)
        return value

    def validate(self, attrs):
        candidate = User(username=attrs["username"], email=attrs.get("email", ""))
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
            # Lost a race against a concurrent signup with the same username.
            raise serializers.ValidationError({"username": [DUPLICATE_USERNAME]}) from exc
