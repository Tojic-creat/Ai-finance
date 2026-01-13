# backend/apps/notifications/mail.py
"""
Email helper utilities for the notifications app.

Provides:
- render_email_from_notification(notification) -> (subject, body, html)
- send_email_via_provider(...) -> sends email synchronously using Django's
  email backend (fallback) or enqueues a Celery task if available.
- send_notification_email(notification_id) -> high-level helper used by tasks
  or views to deliver a Notification instance via email channel and record DeliveryLog.

Design:
- Be defensive: if Celery or a provider-specific task is unavailable, fall back to
  Django's mail.send_mail / EmailMultiAlternatives.
- Do not raise on send failures: return structured dict and ensure DeliveryLog is created.
- Rendering uses simple Python str.format() with the notification.payload as context.
  Avoid executing templates from untrusted sources.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from .models import DeliveryLog, Notification, Channel

logger = logging.getLogger(__name__)


def _safe_format(template: str, context: Dict[str, Any]) -> str:
    """
    Safely format a template string with the provided context.

    We use str.format but catch exceptions — templates should not crash delivery.
    """
    if not template:
        return ""
    try:
        return template.format(**(context or {}))
    except Exception as exc:
        logger.debug(
            "mail._safe_format: formatting failed (%s) for template: %r; returning raw template", exc, template)
        return template


def render_email_from_notification(notification: Notification) -> Tuple[str, str]:
    """
    Ensure the Notification has subject/body filled (rendered) and return them.

    Returns (subject, body). Does NOT save the Notification.
    """
    # Ensure rendering (Notification.render is defensive)
    try:
        notification.render()
    except Exception as exc:
        logger.exception(
            "render_email_from_notification: failed to render notification %s: %s", notification.pk, exc)

    subject = notification.subject or ""
    body = notification.body or ""

    # If settings provide a wrapper template (e.g., HTML or plain wrapper), we can apply it.
    wrapper = getattr(settings, "NOTIFICATIONS_EMAIL_WRAPPER", None)
    if wrapper:
        try:
            # wrapper is expected to be a format string like "{body}\n\n--\n{signature}"
            body = wrapper.format(body=body, **(notification.payload or {}))
        except Exception:
            # fallback to raw body
            pass

    return subject, body


def send_email_via_provider(
    subject: str,
    body: str,
    to_emails: list[str],
    from_email: Optional[str] = None,
    html_body: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    use_celery_task: bool = True,
) -> Dict[str, Any]:
    """
    Sends an email. If a Celery task named `send_email_task` exists in apps.notifications.tasks
    it will be used (if use_celery_task True). Otherwise, send synchronously via Django email backend.

    Returns dict: {"status": "sent" | "scheduled" | "error", "message_id": Optional[str], "error": Optional[str]}
    """
    # Try to enqueue via Celery task if configured and available
    if use_celery_task:
        try:
            from . import tasks as _tasks  # type: ignore
            task = getattr(_tasks, "send_email_task", None)
            if task and hasattr(task, "delay"):
                # Enqueue and return info about enqueuing
                async_res = task.delay(
                    subject, body, to_emails, from_email or settings.DEFAULT_FROM_EMAIL, html_body or "", headers or {})
                return {"status": "scheduled", "task_id": getattr(async_res, "id", None)}
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "send_email_via_provider: could not enqueue celery task (%s), falling back to sync send", exc)

    # Synchronous send using Django Email backend
    try:
        connection = get_connection(fail_silently=False)
        msg = EmailMultiAlternatives(subject=subject, body=body, from_email=from_email or settings.DEFAULT_FROM_EMAIL,
                                     to=to_emails, connection=connection, headers=headers or {})
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        # Django does not return provider message id by default; use a synthetic id (timestamp + logger)
        message_id = f"django-{int(timezone.now().timestamp())}"
        return {"status": "sent", "message_id": message_id}
    except Exception as exc:
        logger.exception(
            "send_email_via_provider: synchronous send failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def send_notification_email(notification_id: int, enqueue_if_supported: bool = True) -> Dict[str, Any]:
    """
    High-level helper to send a Notification (email channel) and record DeliveryLog.

    Behavior:
    - Loads Notification by id.
    - Checks channel == email and can_send().
    - Renders subject/body and attempts to send (via provider or Celery task).
    - Creates a DeliveryLog with the result and updates Notification.status/attempts accordingly.
    - Returns a dict summarizing the outcome.

    This function is safe to call from tasks or synchronously in views (small volume).
    """
    try:
        notification = Notification.objects.select_for_update().get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning(
            "send_notification_email: Notification %s does not exist", notification_id)
        return {"status": "error", "error": "notification_not_found"}

    if notification.channel != Channel.EMAIL:
        return {"status": "error", "error": "wrong_channel", "expected": Channel.EMAIL, "actual": notification.channel}

    if not notification.can_send():
        # record a skipped delivery log for auditability
        DeliveryLog.objects.create(notification=notification, channel=notification.channel, status=DeliveryLog.Status.FAILED,
                                   attempt_time=timezone.now(), provider_response={"reason": "disabled_by_pref_or_settings"})
        return {"status": "skipped", "reason": "disabled"}

    # Render
    subject, body = render_email_from_notification(notification)

    # Optionally html: if payload contains 'html' or template uses wrapper setting
    html_body = None
    if notification.payload and isinstance(notification.payload, dict):
        html_body = notification.payload.get("html") or None

    # Attempt send
    result = send_email_via_provider(subject=subject, body=body, to_emails=[notification.recipient.email] if notification.recipient and notification.recipient.email else [
    ], from_email=None, html_body=html_body, use_celery_task=enqueue_if_supported)

    # Log delivery attempt
    try:
        if result.get("status") == "scheduled":
            # scheduled -> leave Notification as SCHEDULED
            # no-op; kept for clarity
            notification.status = notification.status if notification.status != notification.status else notification.status
            DeliveryLog.objects.create(notification=notification, channel=notification.channel, status=DeliveryLog.Status.PENDING,
                                       attempt_time=timezone.now(), provider_response={"info": "scheduled", "task_id": result.get("task_id")})
            # don't increment attempts here; the actual worker will increment when sending
            return {"status": "scheduled", "task_id": result.get("task_id")}
        elif result.get("status") == "sent":
            # mark sent and create log
            notification.mark_sent(
                provider_message_id=result.get("message_id"))
            DeliveryLog.objects.create(notification=notification, channel=notification.channel, status=DeliveryLog.Status.SENT,
                                       attempt_time=timezone.now(), provider_response={"message_id": result.get("message_id")})
            return {"status": "sent", "message_id": result.get("message_id")}
        else:
            # error path
            err = result.get("error") or "unknown_error"
            # increment attempts and mark failed
            notification.mark_failed(err)
            DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                       status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response={"error": err})
            return {"status": "error", "error": err}
    except Exception as exc:
        # Ensure failures are logged and a DeliveryLog exists
        logger.exception(
            "send_notification_email: error recording delivery log for notification %s: %s", notification_id, exc)
        try:
            DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                       status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response={"error": str(exc)})
        except Exception:
            logger.exception(
                "send_notification_email: also failed to create DeliveryLog for notification %s", notification_id)
        return {"status": "error", "error": str(exc)}
