# backend/apps/notifications/push.py
"""
Push helpers for the notifications app.

Provides:
- render_push_payload(notification) -> dict
- send_push_via_provider(device_tokens, title, body, data, use_celery_task=True) -> dict
- send_notification_push(notification_id) -> dict

Design notes:
- Be defensive: support multiple providers if available (firebase_admin, pyfcm).
- If no provider is available, attempt to enqueue a Celery task named `send_push_task` if present.
- Always create a DeliveryLog for auditability and update Notification status accordingly.
- Do NOT raise on provider errors; return structured dicts instead.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from .models import Channel, DeliveryLog, Notification

logger = logging.getLogger(__name__)


def _safe_format(template: str, context: Dict[str, Any]) -> str:
    """Safely format a template string with the provided context."""
    if not template:
        return ""
    try:
        return template.format(**(context or {}))
    except Exception as exc:
        logger.debug(
            "push._safe_format: formatting failed (%s) for template: %r; returning raw template", exc, template)
        return template


def render_push_payload(notification: Notification) -> Dict[str, Any]:
    """
    Build a push payload from a Notification instance.

    Returns dict with keys:
      - title: str
      - body: str
      - data: dict (custom key/value payload)
      - tokens: list[str] (device tokens) -- may be empty if no devices known
    """
    # Ensure template rendering to populate payload/context
    try:
        notification.render()
    except Exception as exc:
        logger.debug(
            "render_push_payload: notification.render() failed: %s", exc)

    ctx = notification.payload or {}
    title = _safe_format(getattr(notification, "subject", "")
                         or ctx.get("title", ""), ctx)
    body = _safe_format(getattr(notification, "body", "")
                        or ctx.get("body", ""), ctx)
    data = ctx.get("data", {}) if isinstance(ctx.get("data", {}), dict) else {}

    # Attempt to collect device tokens from payload or recipient related field.
    tokens: List[str] = []
    # Preferred: notification.payload["device_tokens"] if provided
    if isinstance(ctx.get("device_tokens"), (list, tuple)):
        tokens = [t for t in ctx.get("device_tokens") if isinstance(t, str)]
    # Fallback: recipient profile attribute (e.g., user.profile.device_tokens)
    if not tokens and notification.recipient is not None:
        try:
            # Common patterns: user.profile.device_token or .device_tokens (list)
            profile = getattr(notification.recipient, "profile", None)
            if profile is not None:
                dev = getattr(profile, "device_token", None) or getattr(
                    profile, "device_tokens", None)
                if isinstance(dev, str):
                    tokens = [dev]
                elif isinstance(dev, (list, tuple)):
                    tokens = [t for t in dev if isinstance(t, str)]
        except Exception:
            # ignore profile failures
            logger.debug(
                "render_push_payload: failed to read recipient profile device tokens")
    return {"title": title, "body": body, "data": data, "tokens": tokens}


def send_push_via_provider(
    device_tokens: List[str],
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
    use_celery_task: bool = True,
) -> Dict[str, Any]:
    """
    Attempt to send a push message to the provided device tokens.

    Tries providers in order:
      1) firebase_admin (messaging)
      2) pyfcm.FCMNotification
      3) Celery task named send_push_task in apps.notifications.tasks (if use_celery_task True)

    Returns a dict with status: "sent"|"scheduled"|"error" and provider-specific info.
    """
    data = data or {}
    if not device_tokens:
        return {"status": "error", "error": "no_device_tokens"}

    # 1) Try firebase_admin.messaging
    try:
        from firebase_admin import messaging as _messaging  # type: ignore

        logger.debug("send_push_via_provider: using firebase_admin.messaging")
        # Prepare a message for multiple tokens via MulticastMessage
        message = _messaging.MulticastMessage(
            notification=_messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in data.items()},
            tokens=device_tokens,
        )
        # If firebase_admin hasn't been initialized in the environment, this may raise.
        response = _messaging.send_multicast(message)
        # response.success_count, response.failure_count and responses list are available
        return {"status": "sent", "provider": "firebase_admin", "success": getattr(response, "success_count", None), "failure": getattr(response, "failure_count", None)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "send_push_via_provider: firebase_admin not available or failed: %s", exc)

    # 2) Try pyfcm (FCMNotification)
    try:
        from pyfcm import FCMNotification  # type: ignore

        logger.debug("send_push_via_provider: using pyfcm.FCMNotification")
        server_key = getattr(settings, "FCM_SERVER_KEY", None)
        if not server_key:
            return {"status": "error", "error": "missing_fcm_server_key"}
        fcm = FCMNotification(api_key=server_key)
        # pyfcm can send to multiple registration_ids
        result = fcm.notify_multiple_devices(
            registration_ids=device_tokens, message_title=title, message_body=body, data_message=data)
        return {"status": "sent", "provider": "pyfcm", "result": result}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "send_push_via_provider: pyfcm not available or failed: %s", exc)

    # 3) If Celery task is available, enqueue it
    if use_celery_task:
        try:
            from . import tasks as _tasks  # type: ignore
            task = getattr(_tasks, "send_push_task", None)
            if task and hasattr(task, "delay"):
                async_res = task.delay(device_tokens, title, body, data)
                return {"status": "scheduled", "provider": "celery_task", "task_id": getattr(async_res, "id", None)}
        except Exception as exc:
            logger.debug(
                "send_push_via_provider: failed to enqueue send_push_task: %s", exc)

    return {"status": "error", "error": "no_push_provider_available"}


def send_notification_push(notification_id: int, enqueue_if_supported: bool = True) -> Dict[str, Any]:
    """
    High-level helper to send a Notification via push channel.

    Steps:
    - Load Notification
    - Validate channel == PUSH and can_send()
    - Render payload and determine device tokens
    - Call send_push_via_provider()
    - Create DeliveryLog and update Notification status accordingly
    """
    try:
        notification = Notification.objects.select_for_update().get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning(
            "send_notification_push: Notification %s not found", notification_id)
        return {"status": "error", "error": "notification_not_found"}

    if notification.channel != Channel.PUSH:
        return {"status": "error", "error": "wrong_channel", "expected": Channel.PUSH, "actual": notification.channel}

    if not notification.can_send():
        DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                   status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response={"reason": "disabled"})
        return {"status": "skipped", "reason": "disabled"}

    payload = render_push_payload(notification)
    tokens = payload.get("tokens", []) or []
    title = payload.get("title", "") or ""
    body = payload.get("body", "") or ""
    data = payload.get("data", {}) or {}

    if not tokens:
        # Nothing to send to; treat as failed but log for audit
        msg = "no_device_tokens"
        notification.mark_failed(msg)
        DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                   status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response={"error": msg})
        return {"status": "error", "error": msg}

    result = send_push_via_provider(
        tokens, title, body, data, use_celery_task=enqueue_if_supported)

    try:
        if result.get("status") == "scheduled":
            # scheduled -> create pending log
            DeliveryLog.objects.create(notification=notification, channel=notification.channel, status=DeliveryLog.Status.PENDING,
                                       attempt_time=timezone.now(), provider_response={"info": "scheduled", "task_id": result.get("task_id")})
            # mark notification as scheduled
            notification.status = Notification.Status.SCHEDULED if hasattr(
                Notification, "Status") else "scheduled"
            notification.save(update_fields=["status", "updated_at"])
            return {"status": "scheduled", "task_id": result.get("task_id")}
        elif result.get("status") == "sent":
            # mark as sent and log
            notification.mark_sent(provider_message_id=result.get(
                "message_id") if result.get("message_id") else None)
            DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                       status=DeliveryLog.Status.SENT, attempt_time=timezone.now(), provider_response=result)
            return {"status": "sent", "info": result}
        else:
            # error path
            err = result.get("error", "unknown_error")
            notification.mark_failed(err)
            DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                       status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response=result)
            return {"status": "error", "error": err}
    except Exception as exc:
        logger.exception(
            "send_notification_push: failed to record delivery for notification %s: %s", notification_id, exc)
        try:
            DeliveryLog.objects.create(notification=notification, channel=notification.channel,
                                       status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response={"error": str(exc)})
        except Exception:
            logger.exception(
                "send_notification_push: failed to create fallback DeliveryLog for notification %s", notification_id)
        return {"status": "error", "error": str(exc)}
