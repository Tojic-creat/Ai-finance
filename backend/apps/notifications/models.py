# backend/apps/notifications/models.py
"""
Models for the notifications app.

This module implements:
- NotificationTemplate: reusable template (subject/body) with metadata.
- NotificationPreference: per-user, per-channel preferences (enabled/disabled).
- Notification: concrete notification instance (scheduled or immediate).
- DeliveryLog: records delivery attempts and provider responses.

Design goals:
- Keep models lightweight and DB-backed so they can be audited and queried.
- Do not embed heavy provider logic here — use tasks (Celery) or services to perform actual sending.
- Provide small helpers (e.g., Notification.send) that attempt a best-effort send using Django mail
  for email, while leaving push/SMS integration to tasks/providers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class Channel(models.TextChoices):
    EMAIL = "email", _("Email")
    PUSH = "push", _("Push")
    SMS = "sms", _("SMS")


class NotificationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    SCHEDULED = "scheduled", _("Scheduled")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class Priority(models.IntegerChoices):
    LOW = 10, _("Low")
    MEDIUM = 50, _("Medium")
    HIGH = 90, _("High")


# ---------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------
class NotificationTemplate(models.Model):
    """
    Reusable notification templates.

    The `body` may contain simple template placeholders using Python str.format(),
    e.g. "Hello {user_name}, your balance is {balance}".

    Use templates for common system messages (low balance, reminder, goal reached).
    """
    key = models.CharField(_("key"), max_length=200, unique=True, help_text=_(
        "Unique template key, e.g. 'low_balance'"))
    name = models.CharField(_("name"), max_length=255, blank=True)
    channel = models.CharField(
        _("channel"), max_length=20, choices=Channel.choices, default=Channel.EMAIL)
    subject = models.CharField(_("subject"), max_length=255, blank=True)
    body = models.TextField(_("body"), help_text=_(
        "Template body. Use Python .format() placeholders."))
    default_payload = models.JSONField(_("default payload"), default=dict, blank=True, help_text=_(
        "Default payload/context for template"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("Notification template")
        verbose_name_plural = _("Notification templates")
        ordering = ("key",)

    def __str__(self) -> str:
        return f"{self.key} ({self.channel})"


# ---------------------------------------------------------------------
# User preferences
# ---------------------------------------------------------------------
class NotificationPreference(models.Model):
    """
    Per-user preferences for notification channels and categories.

    Example usage:
      - user disables email notifications entirely
      - user enables push but disables SMS
      - specific template keys can be muted by setting muted_templates JSON
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notification_preferences")
    channel = models.CharField(
        _("channel"), max_length=20, choices=Channel.choices)
    enabled = models.BooleanField(_("enabled"), default=True)
    # optional map: { "low_balance": False, "goal_achieved": True }
    muted_templates = models.JSONField(
        _("muted templates"), default=dict, blank=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Notification preference")
        verbose_name_plural = _("Notification preferences")
        unique_together = (("user", "channel"),)

    def __str__(self) -> str:
        return f"{self.user} - {self.channel} ({'on' if self.enabled else 'off'})"

    def is_enabled_for(self, template_key: Optional[str] = None) -> bool:
        """
        Return whether notifications are enabled for this channel and optionally for a given template key.
        """
        if not self.enabled:
            return False
        if template_key:
            muted = self.muted_templates or {}
            # if explicitly muted -> disabled for that template
            if template_key in muted:
                return bool(not muted.get(template_key))
        return True


# ---------------------------------------------------------------------
# Notification instances and delivery logs
# ---------------------------------------------------------------------
class Notification(models.Model):
    """
    Concrete notification instance.

    Typical flow:
    - created with status=PENDING or SCHEDULED
    - a background task picks it up, calls .send() (or provider-specific task)
    - DeliveryLog entries record attempts
    """
    template = models.ForeignKey(NotificationTemplate, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="notifications")
    recipient = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(
        _("channel"), max_length=20, choices=Channel.choices)
    subject = models.CharField(_("subject"), max_length=255, blank=True)
    body = models.TextField(_("body"), blank=True)
    payload = models.JSONField(_("payload"), default=dict, blank=True, help_text=_(
        "Context used to render the body/template"))
    priority = models.IntegerField(
        _("priority"), choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(_("status"), max_length=20, choices=NotificationStatus.choices,
                              default=NotificationStatus.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    last_error = models.TextField(_("last error"), blank=True)
    scheduled_at = models.DateTimeField(
        _("scheduled at"), null=True, blank=True, db_index=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ("-priority", "-scheduled_at", "-created_at")
        indexes = [
            models.Index(fields=["recipient", "status"]),
            models.Index(fields=["channel", "status", "scheduled_at"]),
        ]

    def __str__(self) -> str:
        target = self.recipient.get_full_name() if self.recipient else "unknown"
        return f"Notification to {target} [{self.channel}] - {self.status}"

    def render(self) -> None:
        """
        Fill subject/body from template and payload where appropriate.
        Uses simple Python str.format() substitution. This is intentionally lightweight —
        template rendering must be safe (do not allow arbitrary code execution).
        """
        ctx: Dict[str, Any] = {}
        # start with template defaults
        if self.template and (self.template.default_payload or self.template.body or self.template.subject):
            ctx.update(self.template.default_payload or {})
            # then override with instance payload
        ctx.update(self.payload or {})
        # common convenience fields
        if self.recipient:
            ctx.setdefault("user_id", getattr(self.recipient, "pk", None))
            ctx.setdefault("user_name", getattr(
                self.recipient, "get_full_name", lambda: str(self.recipient))())
            ctx.setdefault("user_email", getattr(
                self.recipient, "email", None))

        # Render subject/body if available
        if self.template and not self.subject:
            try:
                self.subject = (self.template.subject or "").format(**ctx)
            except Exception:
                # fallback to raw subject
                self.subject = self.template.subject or ""
        if self.template and not self.body:
            try:
                self.body = (self.template.body or "").format(**ctx)
            except Exception:
                self.body = self.template.body or ""

        # If body/subject still empty, try formatting existing fields
        try:
            if self.subject:
                self.subject = self.subject.format(**ctx)
        except Exception:
            pass
        try:
            if self.body:
                self.body = self.body.format(**ctx)
        except Exception:
            pass

        # Save rendered results (but do not change status)
        self.payload = ctx

    def can_send(self) -> bool:
        """
        Check user preferences and global settings to decide whether to send.
        """
        if not getattr(settings, "NOTIFICATIONS_ENABLED", True):
            return False
        if not self.recipient:
            # system / broadcast messages may be allowed — for now allow only if recipient present
            return False
        # check user preference for channel
        try:
            pref = self.recipient.notification_preferences.filter(
                channel=self.channel).first()
            if pref is not None and not pref.is_enabled_for(self.template.key if self.template else None):
                return False
        except Exception:
            # if preferences table not ready or error, default to allowed
            pass
        return True

    def mark_failed(self, error: str) -> None:
        # use F to avoid race when saving in concurrent tasks
        self.attempts = models.F("attempts") + 1
        self.last_error = error or ""
        self.status = NotificationStatus.FAILED
        self.updated_at = timezone.now()
        # Save using update to respect F expression
        Notification.objects.filter(pk=self.pk).update(
            attempts=self.attempts, last_error=self.last_error, status=self.status, updated_at=self.updated_at)

    def mark_sent(self, provider_message_id: Optional[str] = None) -> None:
        self.status = NotificationStatus.SENT
        self.sent_at = timezone.now()
        self.attempts = models.F("attempts") + 1
        self.updated_at = timezone.now()
        Notification.objects.filter(pk=self.pk).update(
            status=self.status, sent_at=self.sent_at, attempts=self.attempts, updated_at=self.updated_at)
        # optionally log provider_message_id in DeliveryLog by caller

    def send(self, commit: bool = True) -> Dict[str, Any]:
        """
        Attempt to send the notification.

        Behavior:
        - Render template if needed.
        - For email channel we use Django's send_mail as a simple fallback (synchronous).
        - For push/SMS, we return a structured response indicating that a provider/task
          should perform the send.

        The function returns a dict with status and optional details.
        """
        if not self.can_send():
            return {"status": "skipped", "reason": "disabled"}

        # Ensure rendering
        try:
            self.render()
        except Exception as exc:
            # rendering error => mark failed
            self.mark_failed(str(exc))
            return {"status": "error", "error": f"render_failed: {exc}"}

        # Email sending (synchronous fallback)
        if self.channel == Channel.EMAIL:
            recipient_email = None
            if self.recipient:
                recipient_email = getattr(self.recipient, "email", None)
            if not recipient_email:
                self.mark_failed("no_recipient_email")
                return {"status": "error", "error": "no_recipient_email"}

            subject = (self.subject or "")[:255]
            body = self.body or ""
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or None
            try:
                # Simple send; callers should prefer Celery tasks for production sending.
                mail.send_mail(subject=subject, message=body, from_email=from_email, recipient_list=[
                               recipient_email], fail_silently=False)
            except Exception as exc:
                # record failure and return
                self.mark_failed(str(exc))
                # create delivery log
                DeliveryLog.objects.create(notification=self, channel=self.channel,
                                           status=DeliveryLog.Status.FAILED, attempt_time=timezone.now(), provider_response=str(exc))
                return {"status": "error", "error": str(exc)}

            # success
            self.mark_sent(provider_message_id=None)
            # create delivery log
            DeliveryLog.objects.create(notification=self, channel=self.channel, status=DeliveryLog.Status.SENT,
                                       attempt_time=timezone.now(), provider_response="sent_via_django_mail")
            return {"status": "sent"}
        else:
            # For PUSH/SMS we do not implement providers here.
            # Caller (task) is expected to enqueue provider-specific delivery.
            # Mark as SCHEDULED so workers can pick it up.
            self.status = NotificationStatus.SCHEDULED
            if commit:
                self.save(update_fields=["status", "updated_at"])
            return {"status": "scheduled", "info": "deferred_to_provider_task"}


class DeliveryLog(models.Model):
    """
    Log of delivery attempts for a Notification.

    Stores provider responses, message IDs, and the status for each attempt.
    """
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="delivery_logs")
    channel = models.CharField(
        _("channel"), max_length=20, choices=Channel.choices)
    status = models.CharField(_("status"), max_length=20,
                              choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_time = models.DateTimeField(
        _("attempt time"), default=timezone.now)
    provider_response = models.JSONField(_("provider response"), default=dict, blank=True, help_text=_(
        "Raw response from provider (kept for debugging)"))
    provider_message_id = models.CharField(
        _("provider message id"), max_length=255, blank=True)
    error = models.TextField(_("error"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Delivery log")
        verbose_name_plural = _("Delivery logs")
        ordering = ("-attempt_time",)
        indexes = [
            models.Index(fields=["notification", "status"]),
            models.Index(fields=["channel", "status"]),
        ]

    def __str__(self) -> str:
        return f"DeliveryLog(notification={self.notification_id}, channel={self.channel}, status={self.status})"
