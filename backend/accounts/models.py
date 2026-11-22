import unicodedata

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    @classmethod
    def normalize_email(cls, email):
        """Canonical form used for storage, uniqueness checks and login."""
        return unicodedata.normalize("NFKC", super().normalize_email(email)).lower()

    def get_by_natural_key(self, email):
        return self.get(email=self.normalize_email(email))

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified_at", timezone.now())
        if not (extra_fields["is_staff"] and extra_fields["is_superuser"]):
            raise ValueError("Superusers must have is_staff=True and is_superuser=True")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(
        "email address",
        unique=True,
        error_messages={"unique": "A user with that email already exists."},
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # Together with the unique index, this makes emails unique in any case.
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="user_email_lowercase"
            ),
        ]

    @property
    def email_verified(self):
        return self.email_verified_at is not None
