# backend/apps/notifications/__init__.py
"""
Notifications app package for FinAssist.

This module keeps a minimal, import-safe surface for the notifications app.
The app implements email/push/SMS notification plumbing, delivery logs and
retry/backoff logic (in tasks). Heavy initialization (signals, task registration)
is performed in apps.py to avoid side-effects at import time.

Expose a small version identifier for instrumentation and simple checks.
"""
__all__ = ["__version__"]

# Semantic version for the notifications app (bump when making incompatible changes)
__version__ = "0.1.0"
