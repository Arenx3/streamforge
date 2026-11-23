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


def load_secret_key(environ=os.environ):
    """From the environment, or from the file Docker generates on first boot."""
    key = environ.get("DJANGO_SECRET_KEY", "").strip()
    path = environ.get("DJANGO_SECRET_KEY_FILE", "")
    if not key and path:
        try:
            key = Path(path).read_text().strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"Cannot read DJANGO_SECRET_KEY_FILE: {path}") from exc
    if not key or key == "change-me":
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY: it signs sessions and API tokens.")
    return key


SECRET_KEY = load_secret_key()
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
    "drf_spectacular",
    "drf_spectacular_sidecar",
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

# --- HTTPS: off by default because the local stack runs on plain HTTP ---
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT")
# Containers check their own health over HTTP, from inside the network.
SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD")
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE")
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE")

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
CELERY_TASK_ROUTES = {"accounts.*": {"queue": "accounts"}}
CELERY_BEAT_SCHEDULE = {
    "delete-unverified-accounts": {
        "task": "accounts.delete_unverified_accounts",
        "schedule": 3600,
    },
    "abort-abandoned-uploads": {
        "task": "videos.abort_abandoned_uploads",
        "schedule": 3600,
    },
    "recover-stuck-videos": {
        "task": "videos.recover_stuck_videos",
        "schedule": 60,
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
# Lifetime of playback links; must outlast the longest video being watched.
VIDEO_PLAYBACK_URL_EXPIRES = int(env("VIDEO_PLAYBACK_URL_EXPIRES", str(6 * 3600)))
# Public links travel further, so theirs last less; the player renews them on its own.
VIDEO_SHARE_URL_EXPIRES = int(env("VIDEO_SHARE_URL_EXPIRES", "3600"))
# A run without a heartbeat for this long is considered dead and gets restarted.
# Never below three missed heartbeats (30 s each), or live runs would look dead.
VIDEO_STUCK_AFTER_SECONDS = max(int(env("VIDEO_STUCK_AFTER_SECONDS", "180")), 90)
# Redis redelivers unacked tasks after this long, so it must outlast the longest task.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": VIDEO_TRANSCODE_MAX_SECONDS + 7200}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": env("THROTTLE_RATE_LOGIN", "10/minute"),
        "auth_register": env("THROTTLE_RATE_REGISTER", "10/hour"),
        "auth_demo": env("THROTTLE_RATE_DEMO", "60/hour"),
        "auth_email_verify": env("THROTTLE_RATE_EMAIL_VERIFY", "20/hour"),
        "auth_email_resend": env("THROTTLE_RATE_EMAIL_RESEND", "5/hour"),
        "auth_password_reset": env("THROTTLE_RATE_PASSWORD_RESET", "5/hour"),
        "auth_password_confirm": env("THROTTLE_RATE_PASSWORD_CONFIRM", "10/hour"),
        "video_share": env("THROTTLE_RATE_VIDEO_SHARE", "120/minute"),
    },
    # Reverse proxies in front of Django. Rate limits read the client IP from the
    # entry they appended to X-Forwarded-For, so clients cannot spoof it.
    "NUM_PROXIES": int(env("TRUSTED_PROXY_COUNT", "1")),
}

# --- Email ---
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "mailpit")
EMAIL_PORT = int(env("EMAIL_PORT", "1025"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "StreamForge <no-reply@streamforge.local>")
# Base of every link in an email; never derived from the request's Host header.
FRONTEND_URL = env("FRONTEND_URL", "http://localhost").removesuffix(":80")
# Local inbox the app links to. Leave empty when using a real email provider.
MAILBOX_URL = env("MAILBOX_URL", "")
# One-click account with a sample video. Must stay off on a public deployment.
DEMO_ENABLED = env_bool("DEMO_ENABLED")
EMAIL_VERIFICATION_TTL_SECONDS = int(env("EMAIL_VERIFICATION_TTL_SECONDS", str(24 * 3600)))
PASSWORD_RESET_TTL_SECONDS = int(env("PASSWORD_RESET_TTL_SECONDS", "3600"))
ACCOUNT_UNVERIFIED_TTL_HOURS = int(env("ACCOUNT_UNVERIFIED_TTL_HOURS", "72"))
AUTH_EMAIL_COOLDOWN_SECONDS = int(env("AUTH_EMAIL_COOLDOWN_SECONDS", "120"))
# Protects the provider's quota; 0 disables it.
AUTH_EMAIL_DAILY_LIMIT = int(env("AUTH_EMAIL_DAILY_LIMIT", "100"))
# Optional Slack or Discord webhook, called when email delivery fails.
EMAIL_ALERT_WEBHOOK_URL = env("EMAIL_ALERT_WEBHOOK_URL", "")

API_DOCS_ENABLED = env_bool("API_DOCS_ENABLED", True)
SPECTACULAR_SETTINGS = {
    "TITLE": "StreamForge API",
    "DESCRIPTION": "Video upload, HLS processing and private playback.",
    "VERSION": "0.2.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    # Access tokens die with the password they were issued under.
    "CHECK_REVOKE_TOKEN": True,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
