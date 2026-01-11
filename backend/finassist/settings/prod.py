"""
backend/finassist/settings/prod.py

Production settings for FinAssist.

This file imports common settings from base.py and then tightens security and
overrides a few defaults appropriate for production deployments.

Important:
- SECRET_KEY must be provided via environment variable DJANGO_SECRET_KEY.
- DATABASE_URL should be set (Postgres recommended).
- USE_S3 / AWS_* env vars should be set if you want to use S3/MinIO for media/static.
- Configure ALLOWED_HOSTS via DJANGO_ALLOWED_HOSTS (comma separated).
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import *  # noqa: F401,F403

# ---------- Basic sanity ----------
DEBUG = False

# SECRET_KEY must be provided in production - fail early if missing
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY environment variable is required for production!"
    )

# ALLOWED_HOSTS must be set by env (comma-separated). Default: none -> fail early
raw_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
if not raw_hosts:
    # If it's not set, default to an empty list (safer). You can also raise error.
    ALLOWED_HOSTS = []
else:
    ALLOWED_HOSTS = [h.strip() for h in raw_hosts.split(",") if h.strip()]

# If behind a proxy/load balancer that sets X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------- Security ----------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "true").lower() in (
    "1",
    "true",
    "yes",
)
SECURE_HSTS_SECONDS = int(os.environ.get(
    "SECURE_HSTS_SECONDS", 60 * 60 * 24 * 7))  # 1 week by default
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", "true"
).lower() in ("1", "true", "yes")
SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "false").lower() in (
    "1",
    "true",
    "yes",
)
X_FRAME_OPTIONS = "DENY"

# ---------- Static & Media storage ----------
# In prod prefer S3/MinIO; fallback to WhiteNoise (already configured in base)
USE_S3 = os.environ.get("USE_S3", str(USE_S3)).lower() in ("1", "true", "yes")
if USE_S3:
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    STATICFILES_STORAGE = os.environ.get(
        "STATICFILES_STORAGE", "storages.backends.s3boto3.S3Boto3Storage"
    )
    # Required AWS_* variables should be set (base.py reads them)
else:
    # For simple deployments where serve staticfiles from the web container is acceptable:
    STATICFILES_STORAGE = os.environ.get(
        "STATICFILES_STORAGE",
        "whitenoise.storage.CompressedManifestStaticFilesStorage",
    )

# Ensure STATIC_ROOT/MEDIA_ROOT exist
try:
    Path(STATIC_ROOT).mkdir(parents=True, exist_ok=True)
except Exception:
    pass
try:
    Path(MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# ---------- Database ----------
# DATABASES configured in base via DATABASE_URL. Ensure ATOMIC_REQUESTS (important for balance consistency)
DATABASES["default"]["ATOMIC_REQUESTS"] = True

# ---------- Email (production SMTP) ----------
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", os.environ.get("SMTP_HOST", ""))
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", os.environ.get("SMTP_PORT", 25)))
EMAIL_HOST_USER = os.environ.get(
    "EMAIL_HOST_USER", os.environ.get("SMTP_USER", ""))
EMAIL_HOST_PASSWORD = os.environ.get(
    "EMAIL_HOST_PASSWORD", os.environ.get("SMTP_PASSWORD", "")
)
EMAIL_USE_TLS = os.environ.get(
    "EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "FinAssist <noreply@example.com>"
)

# ---------- Caches / Redis ----------
# Redis URL should be set in CELERY_BROKER_URL or REDIS_URL env vars; base.py reads REDIS_URL.
if not REDIS_URL:
    # In prod we strongly recommend setting REDIS_URL
    REDIS_URL = os.environ.get("REDIS_URL", "")
    if REDIS_URL:
        CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": REDIS_URL,
            }
        }

# ---------- Celery (production) ----------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL or "")
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
# Do not run eager tasks in prod
CELERY_TASK_ALWAYS_EAGER = False

# ---------- Logging ----------
# Extend base logging: send errors to Sentry if configured, keep rotating file handler
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOGGING["root"]["level"] = LOG_LEVEL
LOGGING["loggers"]["django"]["level"] = LOG_LEVEL
LOGGING["loggers"]["apps"]["level"] = LOG_LEVEL

# Sentry integration (if SENTRY_DSN is provided)
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    # Lazily configure raven/sentry-sdk if available
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.environ.get(
                "SENTRY_TRACES_SAMPLE_RATE", 0.0)),
            send_default_pii=os.environ.get("SENTRY_SEND_PII", "false").lower() in (
                "1",
                "true",
                "yes",
            ),
        )
    except Exception:
        # If sentry isn't installed, we simply skip initialization (no hard failure)
        pass

# Example: add file handler in production for audit logs if not present
if "file" not in LOGGING["handlers"]:
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(BASE_DIR / "logs" / "finassist.log"),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "verbose",
    }
    LOGGING["root"]["handlers"].append("file")

# ---------- Security / session cookie domain (optional) ----------
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN", None)
CSRF_COOKIE_DOMAIN = os.environ.get("CSRF_COOKIE_DOMAIN", None)

# ---------- Other production tweaks ----------
# Turn off browsable API in production unless explicitly enabled
if not os.environ.get("ENABLE_API_DOCS", "").lower() in ("1", "true", "yes"):
    # remove Browsable API renderer if present
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
        "rest_framework.renderers.JSONRenderer",)

# Performance: purely optional DB connection pooling via pgbouncer or similar is recommended.
# Use connection max age
CONN_MAX_AGE = int(os.environ.get("CONN_MAX_AGE", 60))

# Final note (optional info at startup)
if os.environ.get("CI", "").lower() not in ("1", "true"):
    print("[finassist.settings.prod] Production settings loaded. DEBUG=False")

# End of prod.py
