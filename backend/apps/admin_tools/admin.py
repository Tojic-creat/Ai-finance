# backend/apps/notifications/admin_tools/admin.py
"""
Django admin helpers for the notifications app.

Provides:
- ModelAdmin classes for NotificationTemplate, NotificationPreference, Notification, DeliveryLog
  with sensible list_display, filters and admin actions.
- Admin actions:
  * resend_notifications (delegates to admin_tools.resend_notifications)
  * export_notifications_csv (export selected notifications as CSV)
  * mark_as_sent / mark_as_failed (quick state changes for admin troubleshooting)

Keep this file lightweight and defensive so importing it in the project's admin
won't break when models or Celery are not fully configured.
"""
from __future__ import annotations

import csv
import logging
from typing import Iterable

from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

# Lazy import of helpers/actions from sibling module
try:
    from . import resend_notifications  # function defined in admin_tools/__init__.py
except Exception:
    resend_notifications = None  # type: ignore

# Import models from the notifications app
try:
    from apps.notifications.models import (
        NotificationTemplate,
        NotificationPreference,
        Notification,
        DeliveryLog,
        Channel,
        NotificationStatus,
    )
except Exception as exc:  # pragma: no cover - defensive
    logger.exception(
        "notifications.admin_tools.admin: failed to import notifications models: %s", exc)
    NotificationTemplate = NotificationPreference = Notification = DeliveryLog = Channel = NotificationStatus = None  # type: ignore


# -----------------------
# Common admin actions
# -----------------------
def export_notifications_csv(modeladmin, request, queryset: Iterable[Notification]) -> HttpResponse:
    """
    Export selected Notification rows into a CSV file.
    """
    # Prepare HTTP response with CSV attachment
    now = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"notifications_{now}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    # Header
    writer.writerow(
        [
            "id",
            "recipient_id",
            "recipient_email",
            "channel",
            "status",
            "priority",
            "attempts",
            "scheduled_at",
            "sent_at",
            "created_at",
            "template_key",
            "subject",
            "body",
        ]
    )

    for n in queryset:
        try:
            writer.writerow(
                [
                    getattr(n, "pk", ""),
                    getattr(n.recipient, "pk", "") if getattr(
                        n, "recipient", None) else "",
                    getattr(n.recipient, "email", "") if getattr(
                        n, "recipient", None) else "",
                    getattr(n, "channel", ""),
                    getattr(n, "status", ""),
                    getattr(n, "priority", ""),
                    getattr(n, "attempts", ""),
                    getattr(n, "scheduled_at", ""),
                    getattr(n, "sent_at", ""),
                    getattr(n, "created_at", ""),
                    getattr(n.template, "key", "") if getattr(
                        n, "template", None) else "",
                    (getattr(n, "subject", "") or "").replace("\n", " "),
                    (getattr(n, "body", "") or "").replace("\n", " "),
                ]
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("export_notifications_csv: failed to write row for notification %s: %s", getattr(
                n, "pk", "<unknown>"), exc)

    return response


export_notifications_csv.short_description = "Export selected notifications to CSV"


def mark_notifications_sent(modeladmin, request, queryset: Iterable[Notification]) -> None:
    """
    Admin action: force-mark selected notifications as SENT (for admin troubleshooting).
    Creates a DeliveryLog entry for each change.
    """
    updated = 0
    for n in queryset:
        try:
            n.mark_sent(provider_message_id="admin_marked")
            DeliveryLog.objects.create(notification=n, channel=n.channel, status=DeliveryLog.Status.SENT,
                                       attempt_time=timezone.now(), provider_response={"admin": "marked_sent"})
            updated += 1
        except Exception as exc:
            logger.exception("mark_notifications_sent: failed for %s: %s", getattr(
                n, "pk", "<unknown>"), exc)
    messages.info(request, f"Marked {updated} notification(s) as SENT.")


mark_notifications_sent.short_description = "Mark selected notifications as SENT (admin)"


def mark_notifications_failed(modeladmin, request, queryset: Iterable[Notification]) -> None:
    """
    Admin action: force-mark selected notifications as FAILED (for testing/retries).
    """
    updated = 0
    for n in queryset:
        try:
            n.mark_failed("admin_forced_failure")
            DeliveryLog.objects.create(notification=n, channel=n.channel, status=DeliveryLog.Status.FAILED,
                                       attempt_time=timezone.now(), provider_response={"admin": "marked_failed"})
            updated += 1
        except Exception as exc:
            logger.exception("mark_notifications_failed: failed for %s: %s", getattr(
                n, "pk", "<unknown>"), exc)
    messages.warning(request, f"Marked {updated} notification(s) as FAILED.")


mark_notifications_failed.short_description = "Mark selected notifications as FAILED (admin)"


# -----------------------
# ModelAdmin classes
# -----------------------
if NotificationTemplate is not None:
    @admin.register(NotificationTemplate)
    class NotificationTemplateAdmin(admin.ModelAdmin):
        list_display = ("key", "name", "channel", "active", "updated_at")
        search_fields = ("key", "name", "subject", "body")
        list_filter = ("channel", "active")
        readonly_fields = ("created_at", "updated_at")
        ordering = ("key",)
        fieldsets = (
            (None, {"fields": ("key", "name", "channel", "active")}),
            ("Content", {"fields": ("subject", "body", "default_payload")}),
            ("Meta", {"fields": ("created_at", "updated_at")}),
        )


if NotificationPreference is not None:
    @admin.register(NotificationPreference)
    class NotificationPreferenceAdmin(admin.ModelAdmin):
        list_display = ("user", "channel", "enabled", "updated_at")
        search_fields = ("user__username", "user__email")
        list_filter = ("channel", "enabled")
        readonly_fields = ("updated_at",)
        ordering = ("user", "channel")


if Notification is not None:
    actions = [export_notifications_csv,
               mark_notifications_sent, mark_notifications_failed]
    if resend_notifications is not None:
        actions.insert(0, resend_notifications.resend_notifications)

    @admin.register(Notification)
    class NotificationAdmin(admin.ModelAdmin):
        list_display = ("id", "recipient", "channel", "status", "priority",
                        "attempts", "scheduled_at", "sent_at", "created_at")
        list_filter = ("channel", "status", "priority", "created_at")
        search_fields = ("recipient__username", "recipient__email",
                         "subject", "body", "template__key")
        readonly_fields = ("created_at", "updated_at",
                           "attempts", "sent_at", "last_error")
        ordering = ("-created_at",)
        actions = actions
        fieldsets = (
            (None, {"fields": ("template", "recipient",
             "channel", "priority", "status")}),
            ("Content", {"fields": ("subject", "body", "payload")}),
            ("Delivery", {"fields": ("attempts",
             "last_error", "scheduled_at", "sent_at")}),
            ("Timestamps", {"fields": ("created_at", "updated_at")}),
        )

        def get_queryset(self, request):
            qs = super().get_queryset(request)
            # select_related small optimization
            try:
                return qs.select_related("recipient", "template")
            except Exception:
                return qs


if DeliveryLog is not None:
    @admin.register(DeliveryLog)
    class DeliveryLogAdmin(admin.ModelAdmin):
        list_display = ("id", "notification", "channel",
                        "status", "attempt_time", "created_at")
        list_filter = ("channel", "status", "attempt_time")
        search_fields = ("notification__id", "provider_message_id")
        readonly_fields = ("provider_response", "created_at")
        ordering = ("-attempt_time",)
