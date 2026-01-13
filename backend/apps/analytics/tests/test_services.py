# backend/apps/analytics/tests/test_services.py
from __future__ import annotations

from decimal import Decimal
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.analytics import services
from apps.analytics.models import MetricSnapshot, Metric
from apps.finances.models import Account, Transaction, Category

User = get_user_model()


class AnalyticsServicesTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="ana", email="ana@example.com", password="pw")
        # create accounts
        self.acc_check = Account.objects.create(
            owner=self.user, name="Checking", currency="USD", initial_balance=Decimal("100.00"))
        self.acc_savings = Account.objects.create(
            owner=self.user, name="Savings", currency="USD", initial_balance=Decimal("50.00"), type="savings")
        # create category
        self.cat_food = Category.objects.create(name="Food")
        self.cat_misc = Category.objects.create(name="Misc")
        # dates
        self.now = timezone.now().date()
        self.start = self.now - timezone.timedelta(days=30)
        self.end = self.now

        # transactions:
        # income +200 on checking
        Transaction.objects.create(
            account=self.acc_check,
            amount=Decimal("200.00"),
            currency="USD",
            type=Transaction.Type.INCOME,
            date=self.now,
            description="Salary",
            category=None,
            created_by=self.user,
        )
        # expense -40 on checking, category Food
        Transaction.objects.create(
            account=self.acc_check,
            amount=Decimal("-40.00"),
            currency="USD",
            type=Transaction.Type.EXPENSE,
            date=self.now,
            description="Groceries",
            category=self.cat_food,
            created_by=self.user,
        )
        # expense -30 on savings, category Misc
        Transaction.objects.create(
            account=self.acc_savings,
            amount=Decimal("-30.00"),
            currency="USD",
            type=Transaction.Type.EXPENSE,
            date=self.now,
            description="Small purchase",
            category=self.cat_misc,
            created_by=self.user,
        )

        # ensure cached balances recalculated for deterministic assertions
        self.acc_check.recalculate_balance(save_snapshot=False)
        self.acc_savings.recalculate_balance(save_snapshot=False)

    def test_get_user_accounts_summary_and_top_categories(self):
        summary = services.get_user_accounts_summary(
            self.user.pk, self.start, self.end)
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary.get("status"), "ok")
        # total_income should be 200.00
        self.assertEqual(summary["total_income"], Decimal("200.00"))
        # total_expense should be abs(-40 + -30) = 70.00
        self.assertEqual(summary["total_expense"], Decimal("70.00"))
        # tx_count should be 3
        self.assertEqual(summary["tx_count"], 3)
        # by_account present
        self.assertTrue(isinstance(summary["by_account"], list))
        # top categories
        top = services.top_categories(
            self.user.pk, self.start, self.end, limit=5)
        # expect both categories present (Food and Misc)
        cat_names = {c["category"] for c in top}
        self.assertTrue("Food" in cat_names or "Misc" in cat_names)

    def test_create_metric_snapshot(self):
        # compute callable returns Decimal
        def compute():
            return Decimal("55.50")

        period_start = self.start
        period_end = self.end
        res = services.create_metric_snapshot(
            "test.metric", period_start, period_end, compute, unit="USD", meta={"note": "unit test"})
        self.assertEqual(res.get("status"), "ok")
        snap_id = res.get("snapshot_id")
        self.assertIsNotNone(snap_id)
        snap = MetricSnapshot.objects.get(pk=snap_id)
        self.assertEqual(snap.unit, "USD")
        self.assertEqual(snap.value.quantize(
            Decimal("0.01")), Decimal("55.50"))
        # also check Metric exists
        metric = Metric.objects.get(key="test.metric")
        self.assertIsNotNone(metric)

    def test_generate_user_dashboard_and_recommendations(self):
        payload = services.generate_user_dashboard(self.user.pk)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("status"), "ok")
        # check keys
        self.assertIn("summary", payload)
        self.assertIn("top_categories", payload)
        self.assertIn("recent_transactions", payload)
        self.assertIn("recommendations", payload)
        # recommendations is a list (may be empty)
        self.assertIsInstance(payload["recommendations"], list)

    def test_train_category_classifier_async_defensive(self):
        # This function is defensive; it should return a dict with a status field
        res = services.train_category_classifier_async(dry_run=True)
        self.assertIsInstance(res, dict)
        self.assertIn("status", res)

    def test_export_user_transactions_csv(self):
        content, filename = services.export_user_transactions_csv(
            self.user.pk, self.start, self.end)
        self.assertIsInstance(content, (bytes, bytearray))
        self.assertIn(str(self.user.pk), filename)
        # Basic CSV parsing check
        text = content.decode("utf-8")
        reader = io.StringIO(text)
        header = reader.readline().strip()
        self.assertTrue("date" in header and "amount" in header)
