"""
backend/finassist/settings/base.py

Базовые настройки Django для проекта FinAssist.

Это "общая" конфигурация, общая для dev/prod/staging. Конкретные
переменные окружения (секретный ключ, DATABASE_URL, DEBUG и т.д.)
должны задаваться в окружении или в файлах настроек `dev.py`/`prod.py`,
которые импортируют (`from .base import *`) и при необходимости переопределяют.

Зависимости:
- django-environ (используется для удобного чтения env vars)
- whitenoise (опционально для простого static serving в контейнере)
- django-storages + boto3 (если используете S3/MinIO)
"""

from __future__ import annotations

import os
from pathlib import Path

import environ

# ================
# Paths
# ================
# backend/.. -> repo root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_DIR = Path(__file__).resolve().parent.parent  # backend/finassist

# ================
# Environment
# ================
env = environ.Env(
    # set casting, default
    DJANGO_DEBUG=(bool, False),
    DJANGO_ENV=(str, "dev"),
)

# If there's a .env file in project root or backend/, load it silently.
# manage.py already attempts to load .env via python-dotenv but this is idempotent.
env.read_env(env.str("ENV_FILE", str(BASE_DIR / ".env"))
             if os.path.exists(BASE_DIR / ".env") else None)

# ================
# Core settings
# ================
SECRET_KEY = env("DJANGO_SECRET_KEY", default="changeme-in-dev-or-set-ENV")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ENVIRONMENT = env.str("DJANGO_ENV", default="dev")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[
                         "localhost", "127.0.0.1"])

# Application definition
INSTALLED_APPS = [
    # Django contrib
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "django_celery_beat",  # optional, useful for scheduled tasks
    "storages",  # django-storages
    "corsheaders",
    "drf_yasg",
    # Project apps (example)
    "apps.finances",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # whitenoise should be placed after SecurityMiddleware and before CommonMiddleware
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "finassist.urls"

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(PROJECT_DIR / "templates"), str(BASE_DIR / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "finassist.wsgi.application"
ASGI_APPLICATION = "finassist.asgi.application"

# ================
# Database
# ================
# Use DATABASE_URL env var when provided, otherwise sqlite for local/dev
# Example DATABASE_URL: postgres://user:pass@host:5432/dbname
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{str(BASE_DIR / 'db.sqlite3')}",
    )
}
# Ensure atomic requests for consistency when updating balances/transactions
DATABASES["default"]["ATOMIC_REQUESTS"] = True

# ================
# Password validation
# ================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ================
# Internationalization / Timezone
# ================
LANGUAGE_CODE = env.str("DJANGO_LANGUAGE_CODE", default="en-us")
TIME_ZONE = env.str("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_L10N = True
USE_TZ = True

# ================
# Static & Media
# ================
STATIC_URL = env.str("STATIC_URL", "/static/")
MEDIA_URL = env.str("MEDIA_URL", "/media/")

STATIC_ROOT = env.str("STATIC_ROOT", str(BASE_DIR / "staticfiles"))
MEDIA_ROOT = env.str("MEDIA_ROOT", str(BASE_DIR / "media"))

# Whitenoise for static files in simple deployments
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ================
# File storage (S3 / MinIO) - optional
# ================
USE_S3 = env.bool("USE_S3", default=False)
if USE_S3:
    # django-storages settings
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
    AWS_S3_SIGNATURE_VERSION = env("AWS_S3_SIGNATURE_VERSION", default="s3v4")
    AWS_DEFAULT_ACL = None
else:
    DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"

# ================
# Caches
# ================
REDIS_URL = env("REDIS_URL", default=None)
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }

# ================
# Celery
# ================
# Celery settings - broker/result backend come from REDIS_URL or env vars
CELERY_BROKER_URL = env("CELERY_BROKER_URL",
                        default=REDIS_URL or "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ================
# Django REST Framework
# ================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": env.int("DRF_PAGE_SIZE", default=50),
}

# ================
# CORS
# ================
CORS_ALLOW_ALL_ORIGINS = env.bool(
    "CORS_ALLOW_ALL_ORIGINS", default=(DEBUG is True))
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ================
# Email
# ================
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend" if not DEBUG else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL",
                         default="FinAssist <noreply@example.com>")

# ================
# Security defaults
# ================
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

# ================
# Logging
# ================
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s"},
        "simple": {"format": "%(levelname)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "logs" / "finassist.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": True},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# Ensure logs folder exists (best-effort)
try:
    os.makedirs(str(BASE_DIR / "logs"), exist_ok=True)
except Exception:
    pass

# ================
# Misc / Defaults
# ================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Pagination, uploads
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int(
    "DATA_UPLOAD_MAX_MEMORY_SIZE", default=5242880)  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = env.int(
    "FILE_UPLOAD_MAX_MEMORY_SIZE", default=10 * 1024 * 1024)  # 10 MB

# Maximum attachment size (aligns with adjustment / transaction file limits)
MAX_ATTACHMENT_SIZE = env.int(
    "MAX_ATTACHMENT_SIZE", default=10 * 1024 * 1024)  # 10 MB

# ================
# Third-party / feature flags (placeholders)
# ================
# Sentry DSN (optional)
SENTRY_DSN = env("SENTRY_DSN", default="")

# Feature flags (simple)
FEATURE_FLAGS = {
    "ENABLE_GAMIFICATION": env.bool("ENABLE_GAMIFICATION", default=False),
    "ENABLE_ML": env.bool("ENABLE_ML", default=True),
}

# ================
# End of file
# ================
