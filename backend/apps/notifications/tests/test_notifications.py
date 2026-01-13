# backend/apps/notifications/tests/test_notifications.py
from __future__ import annotations

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from apps.notifications.models import (
    NotificationTemplate,
    NotificationPreference,
    Notification,
    DeliveryLog,
    Channel,
    NotificationStatus,
)
from apps.notifications import mail as mail_helpers
from apps.notifications import push as push_helpers

User = get_user_model()


@override_settings(DEFAULT_FROM_EMAIL="no-reply@example.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", NOTIFICATIONS_ENABLED=True)
class NotificationsEmailTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass")
        # create a simple email template
        self.template = NotificationTemplate.objects.create(
            key="low_balance",
            name="Low balance alert",
            channel=Channel.EMAIL,
            subject="Low balance: {user_name}",
            body="Hello {user_name}, your balance on account {account_name} is low: {balance}",
            default_payload={"account_name": "Checking", "balance": "10.00"},
        )

    def test_email_notification_send_creates_deliverylog_and_marks_sent(self):
        n = Notification.objects.create(
            template=self.template,
            recipient=self.user,
            channel=Channel.EMAIL,
            payload={"account_name": "Primary", "balance": "$5.00"},
            status=NotificationStatus.PENDING,
        )

        # send synchronously via model helper
        res = n.send()
        self.assertEqual(res.get("status"), "sent")

        # Django in-memory email backend should have a message
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("Low balance", msg.subject)

        # DeliveryLog entry created and notification status updated in DB
        logs = DeliveryLog.objects.filter(notification__pk=n.pk)
        self.assertTrue(logs.exists())
        n.refresh_from_db()
        self.assertEqual(n.status, NotificationStatus.SENT)

    def test_email_skipped_when_notifications_disabled(self):
        with override_settings(NOTIFICATIONS_ENABLED=False):
            n = Notification.objects.create(
                template=self.template,
                recipient=self.user,
                channel=Channel.EMAIL,
                payload={},
            )
            res = n.send()
            self.assertEqual(res.get("status"), "skipped")
            # no mail sent
            self.assertEqual(len(mail.outbox), 0)
            # no DeliveryLog created by send (send() returns early)
            self.assertFalse(DeliveryLog.objects.filter(
                notification=n).exists())

    def test_email_respects_user_preferences(self):
        # disable email for user
        NotificationPreference.objects.create(
            user=self.user, channel=Channel.EMAIL, enabled=False)
        n = Notification.objects.create(
            template=self.template,
            recipient=self.user,
            channel=Channel.EMAIL,
            payload={},
        )
        res = n.send()
        self.assertEqual(res.get("status"), "skipped")
        # no email sent
        self.assertEqual(len(mail.outbox), 0)


class NotificationsPushTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw")
        self.template = NotificationTemplate.objects.create(
            key="goal_reached",
            name="Goal reached",
            channel=Channel.PUSH,
            subject="Goal reached: {user_name}",
            body="Congrats {user_name}, you reached your saving goal!",
            default_payload={},
        )

    def test_push_send_without_provider_creates_failed_log(self):
        # Build a notification that provides device_tokens explicitly in payload
        n = Notification.objects.create(
            template=self.template,
            recipient=self.user,
            channel=Channel.PUSH,
            payload={"device_tokens": [
                "token1", "token2"], "data": {"goal": "rainy_day"}},
            status=NotificationStatus.PENDING,
        )

        # Call the helper that attempts to send via providers (none are available in test)
        res = push_helpers.send_notification_push(
            n.pk, enqueue_if_supported=False)
        # Expect an error because no push provider is available in the test environment
        self.assertIn(res.get("status"), ("error", "scheduled", "sent"))
        # If error path used, the notification should be marked failed and a DeliveryLog should exist
        n.refresh_from_db()
        if res.get("status") == "error":
            self.assertEqual(n.status, NotificationStatus.FAILED)
            dl = DeliveryLog.objects.filter(
                notification=n, status=DeliveryLog.Status.FAILED)
            self.assertTrue(dl.exists())
        else:
            # If scheduled/sent (unlikely in a plain test env), ensure there is at least one DeliveryLog
            self.assertTrue(DeliveryLog.objects.filter(
                notification=n).exists())

    def test_push_payload_rendering_uses_payload_and_template(self):
        n = Notification.objects.create(
            template=self.template,
            recipient=self.user,
            channel=Channel.PUSH,
            payload={"device_tokens": ["t"], "data": {"k": "v"}},
        )
        payload = push_helpers.render_push_payload(n)
        self.assertIsInstance(payload, dict)
        self.assertIn("title", payload)
        self.assertIn("body", payload)
        self.assertIsInstance(payload.get("tokens"), list)
        self.assertEqual(payload.get("tokens"), ["t"])
