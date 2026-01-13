# backend/apps/notifications/tasks.py
"""
Celery tasks for the notifications app.

Provides:
- send_email_task: wrapper task that sends raw email data (used by mail.send_email_via_provider).
- send_push_task: wrapper task that sends push payloads (used by push.send_push_via_provider).
- send_notification_task: high-level task that sends a Notification instance by id (email/push/SMS).
- dispatch_scheduled_notifications: periodic task to pick scheduled/pending notifications and enqueue send_notification_task.

Tasks are defensive:
- use retries with exponential backoff for transient errors
- always log failures and create DeliveryLog entries via the helper functions
- avoid recursive enqueueing by delegating to provider helpers with enqueue_if_supported=False when running as a worker
"""

from __future__ import annotations
from .models import DeliveryLog, Notification, Channel
from . import push as push_helpers  # type: ignore
from . import mail as mail_helpers  # type: ignore

import logging
from typing import Any, Dict, Iterable, List, Optional

from celery import shared_task, Task
from celery.utils.log import get_task_logger
from django.db import transaction
from django.utils import timezone

logger = get_task_logger(__name__)
DEFAULT_BATCH = 50  # how many scheduled notifications to dispatch per run

# Import helpers from this app. They are implemented defensively.

# Generic retry parameters
MAX_RETRIES = 5
INITIAL_COUNTDOWN = 30  # seconds


@shared_task(bind=True, max_retries=MAX_RETRIES, name="notifications.send_email_task")
def send_email_task(self: Task, subject: str, body: str, to_emails: List[str], from_email: Optional[str] = None, html_body: Optional[str] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Celery task wrapper to send email via the provider helper.
    This task is intentionally simple — the heavy lifting is in mail.send_email_via_provider.
    """
    try:
        res = mail_helpers.send_email_via_provider(subject=subject, body=body, to_emails=to_emails,
                                                   from_email=from_email, html_body=html_body, headers=headers or {}, use_celery_task=False)
        return res
    except Exception as exc:
        # Retry with exponential backoff
        logger.exception("send_email_task failed: %s", exc)
        try:
            countdown = INITIAL_COUNTDOWN * (2 ** self.request.retries)
            self.retry(exc=exc, countdown=countdown)
        except Exception:
            # If retry is exhausted or fails, return error
            return {"status": "error", "error": str(exc)}


@shared_task(bind=True, max_retries=MAX_RETRIES, name="notifications.send_push_task")
def send_push_task(self: Task, device_tokens: List[str], title: str, body: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Celery task wrapper to send push notifications via provider helper.
    """
    try:
        res = push_helpers.send_push_via_provider(
            device_tokens=device_tokens, title=title, body=body, data=data or {}, use_celery_task=False)
        return res
    except Exception as exc:
        logger.exception("send_push_task failed: %s", exc)
        try:
            countdown = INITIAL_COUNTDOWN * (2 ** self.request.retries)
            self.retry(exc=exc, countdown=countdown)
        except Exception:
            return {"status": "error", "error": str(exc)}


@shared_task(bind=True, max_retries=MAX_RETRIES, name="notifications.send_notification_task")
def send_notification_task(self: Task, notification_id: int, force: bool = False) -> Dict[str, Any]:
    """
    High-level task to deliver a Notification instance by id.

    - Loads Notification (select_for_update to avoid races).
    - Checks channel and delegates to the appropriate helper.
    - Creates/updates DeliveryLog entries via helpers.
    - Retries on unexpected exceptions.
    """
    try:
        # Acquire DB lock to avoid concurrent workers sending the same notification
        with transaction.atomic():
            try:
                notification = Notification.objects.select_for_update().get(pk=notification_id)
            except Notification.DoesNotExist:
                logger.warning(
                    "send_notification_task: Notification %s not found", notification_id)
                return {"status": "error", "error": "notification_not_found"}

            # If not forced and notification is already sent/cancelled, skip
            if not force and notification.status in (Notification.Status.SENT if hasattr(Notification, "Status") else "sent", Notification.Status.CANCELLED if hasattr(Notification, "Status") else "cancelled"):
                return {"status": "skipped", "reason": f"status_{notification.status}"}

            # If scheduled_at is set and in the future, skip (reschedule)
            if notification.scheduled_at and notification.scheduled_at > timezone.now() and not force:
                logger.debug("send_notification_task: Notification %s scheduled in future (%s), skipping",
                             notification_id, notification.scheduled_at)
                return {"status": "skipped", "reason": "scheduled_in_future"}

            # Based on channel, call appropriate helper. Ensure we tell helpers not to re-enqueue tasks.
            if notification.channel == Channel.EMAIL:
                result = mail_helpers.send_notification_email(
                    notification_id=notification_id, enqueue_if_supported=False)
            elif notification.channel == Channel.PUSH:
                result = push_helpers.send_notification_push(
                    notification_id=notification_id, enqueue_if_supported=False)
            else:
                # For SMS or unknown channels, attempt to call generic send (not implemented) and mark failed
                logger.warning("send_notification_task: Unsupported channel %s for notification %s",
                               notification.channel, notification_id)
                # create a DeliveryLog entry for audit
                DeliveryLog.objects.create(notification=notification, channel=notification.channel, status=DeliveryLog.Status.FAILED,
                                           attempt_time=timezone.now(), provider_response={"error": "unsupported_channel"})
                # mark notification as failed
                notification.mark_failed(
                    f"unsupported_channel:{notification.channel}")
                result = {"status": "error", "error": "unsupported_channel"}

            return result
    except Exception as exc:
        logger.exception(
            "send_notification_task: unexpected error sending notification %s: %s", notification_id, exc)
        # Retry with exponential backoff
        try:
            countdown = INITIAL_COUNTDOWN * (2 ** self.request.retries)
            self.retry(exc=exc, countdown=countdown)
        except Exception:
            return {"status": "error", "error": str(exc)}


@shared_task(bind=True, name="notifications.dispatch_scheduled_notifications")
def dispatch_scheduled_notifications(self: Task, batch: int = DEFAULT_BATCH) -> Dict[str, Any]:
    """
    Periodic task (should be scheduled in Celery beat) that picks up scheduled/pending notifications
    whose scheduled_at <= now (or without scheduled_at) and enqueues send_notification_task for them.

    The function returns summary counts.
    """
    now = timezone.now()
    dispatched = 0
    errors = 0
    try:
        # Select notifications that are SCHEDULED or PENDING and ready to send.
        qs = Notification.objects.filter(
            status__in=[Notification.Status.SCHEDULED, Notification.Status.PENDING] if hasattr(
                Notification, "Status") else ["scheduled", "pending"]
        ).filter(models.Q(scheduled_at__lte=now) | models.Q(scheduled_at__isnull=True)).order_by("scheduled_at", "created_at")[:batch]

        ids = [n.pk for n in qs]
        for nid in ids:
            try:
                send_notification_task.delay(nid)
                dispatched += 1
            except Exception as exc:
                logger.exception(
                    "dispatch_scheduled_notifications: failed to enqueue send_notification_task for %s: %s", nid, exc)
                errors += 1
        return {"status": "ok", "dispatched": dispatched, "errors": errors}
    except Exception as exc:
        logger.exception(
            "dispatch_scheduled_notifications: failed to dispatch scheduled notifications: %s", exc)
        return {"status": "error", "error": str(exc), "dispatched": dispatched, "errors": errors}
