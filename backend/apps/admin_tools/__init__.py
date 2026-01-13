# backend/apps/notifications/admin_tools/__init__.py
"""
Admin helper utilities for the notifications app.

Keep this module minimal and import-safe. It provides a couple of small helpers
that can be used from Notification ModelAdmin definitions in admin.py without
causing heavy imports or side-effects at module import time.

Example usage in admin.py:
    from apps.notifications.admin_tools import resend_notifications
    @admin.register(Notification)
    class NotificationAdmin(admin.ModelAdmin):
        actions = [resend_notifications, ...]
"""
from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

__all__ = ["__version__", "resend_notifications"]

# semantic version for the admin-tools helper module
__version__ = "0.1.0"


def _enqueue_send(notification_id: int) -> dict:
    """
    Try to enqueue the notification send task via Celery if available,
    otherwise attempt to call the task function synchronously.

    Returns a small dict with status info for logging.
    """
    try:
        # Prefer Celery async enqueue if present
        from apps.notifications import tasks as _tasks  # type: ignore

        send_task = getattr(_tasks, "send_notification_task", None)
        if send_task and hasattr(send_task, "delay"):
            async_res = send_task.delay(notification_id)
            return {"method": "celery", "task_id": getattr(async_res, "id", None)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "admin_tools._enqueue_send: unable to enqueue via Celery (%s)", exc)

    # Fallback: call the task function synchronously if available
    try:
        from apps.notifications.tasks import send_notification_task  # type: ignore

        # Call synchronously (not ideal for production, but useful for admin)
        res = send_notification_task(notification_id, force=True)
        return {"method": "sync", "result": res}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "admin_tools._enqueue_send: fallback synchronous call failed: %s", exc)
        return {"method": "error", "error": str(exc)}


def resend_notifications(modeladmin, request, queryset: Iterable) -> None:
    """
    Admin action: enqueue resend for selected Notification instances.

    - Attempts to enqueue via Celery when possible, otherwise falls back to synchronous call.
    - Logs outcomes using Django's logging. Does not raise.
    """
    # Import lazily to avoid import-time model coupling
    try:
        from apps.notifications.models import Notification  # type: ignore
    except Exception:
        Notification = None  # type: ignore

    count = 0
    results = []
    for obj in queryset:
        try:
            nid = int(getattr(obj, "pk"))
        except Exception:
            logger.debug(
                "admin_tools.resend_notifications: skipping object without pk: %r", obj)
            continue
        res = _enqueue_send(nid)
        results.append((nid, res))
        count += 1

    # Provide a short admin message via Django's message framework (if available).
    try:
        from django.contrib import messages

        if count:
            messages.info(
                request, f"Enqueued resend for {count} notification(s).")
        else:
            messages.warning(request, "No notifications were enqueued.")
    except Exception:
        # If messages framework not available in this context, just log.
        logger.info(
            "resend_notifications: enqueued %d notifications; details: %s", count, results)


# Human-friendly label shown in the Django admin "Actions" dropdown
resend_notifications.short_description = "Resend selected notifications (enqueue)"
