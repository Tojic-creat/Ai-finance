"""
WSGI config for FinAssist project.

This module exposes the WSGI callable as a module-level variable named ``application``.
It is used by WSGI servers such as Gunicorn.

Notes:
- Default DJANGO_SETTINGS_MODULE is set to "finassist.settings.prod" because
  WSGI is typically used in production (override via env var in dev/CI if needed).
- Optionally wraps the Django WSGI app with WhiteNoise (if available) to serve
  static files directly from the application container in simple deployments.
"""

from django.core.wsgi import get_wsgi_application
import os

# Set a sensible default settings module for WSGI (production). Can be overridden by env.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finassist.settings.prod")

# Optional diagnostics for interactive runs (won't print in CI)
if os.environ.get("CI", "").lower() not in ("1", "true"):
    try:
        print(
            f"Using settings module: {os.environ.get('DJANGO_SETTINGS_MODULE')}")
    except Exception:
        pass


_application = get_wsgi_application()

# Try to wrap with WhiteNoise if available (useful for simple deployments)
try:
    from whitenoise import WhiteNoise

    application = WhiteNoise(_application)
except Exception:
    # If whitenoise isn't installed or fails to import, fall back to plain WSGI app
    application = _application
