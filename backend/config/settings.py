import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(key, default=None):
    value = os.environ.get(key, default)
    if value is None:
        raise ImproperlyConfigured(f"Missing required environment variable: {key}")
    return value


def env_bool(key, default=False):
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key, default=""):
    return [item.strip() for item in os.environ.get(key, default).split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY")
if SECRET_KEY == "change-me":
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY: it signs sessions and API tokens.")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "core",
    "videos",
    "accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "postgres"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Behind nginx reverse proxy
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Redis / Celery ---
REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "abort-abandoned-uploads": {
        "task": "videos.abort_abandoned_uploads",
        "schedule": 3600,
    },
}

# Shared across gunicorn workers so rate limits hold. Uses its own Redis DB because
# cache.clear() runs FLUSHDB, and DBs 0 and 1 hold the Celery queue and results.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_REDIS_URL", urlsplit(REDIS_URL)._replace(path="/2").geturl()),
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}

# --- S3 (RustFS local / Cloudflare R2 prod) ---
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL")
# Host browsers use for presigned URLs; differs from the internal one under Docker.
AWS_S3_PUBLIC_ENDPOINT_URL = env("AWS_S3_PUBLIC_ENDPOINT_URL", AWS_S3_ENDPOINT_URL)
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", "us-east-1")

STREAMFORGE_TMP_DIR = env("STREAMFORGE_TMP_DIR", "/tmp/streamforge")

# --- Video uploads ---
VIDEO_UPLOAD_MAX_BYTES = int(env("VIDEO_UPLOAD_MAX_BYTES", str(5 * 1024**3)))
VIDEO_UPLOAD_URL_EXPIRES = int(env("VIDEO_UPLOAD_URL_EXPIRES", str(6 * 3600)))
# Must exceed the URL lifetime so an upload in progress is never aborted.
VIDEO_UPLOAD_ABANDON_HOURS = int(env("VIDEO_UPLOAD_ABANDON_HOURS", "24"))

# --- Video processing ---
VIDEO_MAX_DURATION_SECONDS = int(env("VIDEO_MAX_DURATION_SECONDS", str(4 * 3600)))
VIDEO_TRANSCODE_MAX_SECONDS = int(env("VIDEO_TRANSCODE_MAX_SECONDS", str(6 * 3600)))
# Redis redelivers unacked tasks after this long, so it must outlast the longest task.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": VIDEO_TRANSCODE_MAX_SECONDS + 7200}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": env("THROTTLE_RATE_LOGIN", "10/minute"),
        "auth_register": env("THROTTLE_RATE_REGISTER", "10/hour"),
    },
    # Reverse proxies in front of Django. Rate limits read the client IP from the
    # entry they appended to X-Forwarded-For, so clients cannot spoof it.
    "NUM_PROXIES": int(env("TRUSTED_PROXY_COUNT", "1")),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
