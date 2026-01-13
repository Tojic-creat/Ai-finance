# backend/apps/analytics/__init__.py
"""
Analytics app package for FinAssist.

This module intentionally keeps a minimal surface area. The `analytics` app
contains lightweight reporting utilities, metrics collectors, and connectors
to the ML/BI pipeline (e.g. batch jobs, aggregated indicators).

Keep initialization minimal to avoid heavy imports at import-time. App-level
startup work (signals, task registration) should be performed inside the
AppConfig.ready() implementation in apps.py.

Expose a small version identifier for instrumentation and debugging.
"""
__all__ = []

# Semantic version for the analytics app (bumped when making breaking changes to the app)
__version__ = "0.1.0"
