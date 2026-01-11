"""
backend/finassist/settings/dev.py

Development settings for FinAssist.

This file imports all defaults from `base.py` and then overrides
a subset of settings for local development convenience.

- DEBUG = True
- Console email backend
- SQLite default DB (unless DATABASE_URL provided)
- Enable django-debug-toolbar if installed
- CORS relaxed for local testing
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import *  # noqa: F401,F403

# BASE_DIR available from base.py
# Ensure we don't accidentally run dev settings in production
ENV = os.environ.get("DJANGO_ENV", os.environ.get("ENVIRONMENT", "dev"))
if ENV not in ("dev", "development", ""):
    # Not raising — just a warning in dev environments
    print(f"[finassist.settings.dev] DJANGO_ENV={ENV} (expected 'dev')")

# Debug / Hosts
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
CORS_ALLOW_ALL_ORIGINS = True  # relax for local dev
CSRF_TRUSTED_ORIGINS = ["http://localhost", "http://127.0.0.1"]

# Secret key for dev (override via env if you want)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-change-me")

# Database: prefer DATABASE_URL if provided, otherwise use a simple sqlite for dev
if os.environ.get("DATABASE_URL"):
    # base.py already configured DATABASES from env.DATABASE_URL if present,
    # so we only set sqlite when DATABASE_URL is not provided.
    pass
else:
    SQLITE_PATH = Path(BASE_DIR) / "db.sqlite3"
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(SQLITE_PATH),
        }
    }

# Email: console backend for dev to avoid sending real email
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 1025))

# Static files in dev: serve from local static root; whitenoise still works if present
STATICFILES_DIRS = [str(Path(BASE_DIR) / "static")]

# File storage: local filesystem (ensure DEFAULT_FILE_STORAGE already set in base)
DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"

# Debug toolbar (optional)
INSTALLED_APPS += [
    # Add debug toolbar if available; safe to include even if not installed.
    "debug_toolbar",
]

MIDDLEWARE = [
    # debug toolbar should be as early as possible after security/session middleware
    "debug_toolbar.middleware.DebugToolbarMiddleware",
] + MIDDLEWARE

# internal ips for debug toolbar
INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Simple logging override for dev (more verbose)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")
LOGGING["root"]["level"] = LOG_LEVEL
if "console" in LOGGING["handlers"]:
    LOGGING["handlers"]["console"]["formatter"] = "verbose"
# Make Django logger verbose in dev
LOGGING["loggers"]["django"]["level"] = LOG_LEVEL
LOGGING["loggers"]["apps"]["level"] = LOG_LEVEL

# Celery in dev: use Redis if REDIS_URL provided; otherwise use synchronous tasks by default
# (this helps when developer doesn't want to run a broker)
if not os.environ.get("CELERY_BROKER_URL") and not os.environ.get("REDIS_URL"):
    # run tasks synchronously in dev to simplify local development
    CELERY_TASK_ALWAYS_EAGER = True  # type: ignore[name-defined]
    CELERY_TASK_EAGER_PROPAGATES = True  # type: ignore[name-defined]
else:
    CELERY_TASK_ALWAYS_EAGER = False  # type: ignore[name-defined]

# Useful dev helpers / feature flags
FEATURE_FLAGS["ENABLE_GAMIFICATION"] = os.environ.get("ENABLE_GAMIFICATION", "false").lower() in (
    "1",
    "true",
    "yes",
)
FEATURE_FLAGS["ENABLE_ML"] = os.environ.get(
    "ENABLE_ML", "true").lower() in ("1", "true", "yes")

# DRF debug tweaks
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

# Swagger / API docs should be visible in dev
ENABLE_API_DOCS = True

# Convenience: show a small message when settings are loaded interactively
if os.environ.get("CI", "").lower() not in ("1", "true"):
    print("[finassist.settings.dev] Development settings loaded (DEBUG=True)")

# End of dev settings
