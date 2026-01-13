# backend/apps/finances/tests/test_models.py
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ..models import (
    Account,
    Adjustment,
    AuditLog,
    BalanceSnapshot,
    Category,
    Goal,
    ImportJob,
    Transaction,
)

User = get_user_model()


class FinancesModelsTestCase(TestCase):
    def setUp(self) -> None:
        # create a test user and two accounts
        self.user = User.objects.create_user(
            username="tester", email="tester@example.com", password="pw")
        self.acc1 = Account.objects.create(
            owner=self.user, name="Checking", currency="USD", initial_balance=Decimal("100.00"))
        self.acc2 = Account.objects.create(
            owner=self.user, name="Savings", currency="USD", initial_balance=Decimal("50.00"))

    def test_account_get_balance_and_recalculate_snapshot(self):
        """
        get_balance should reflect initial_balance + transactions + adjustments.
        recalculate_balance should update cached balance and create a BalanceSnapshot (when requested).
        """
        # initial balances (no transactions/adjustments yet)
        self.assertEqual(self.acc1.get_balance(), Decimal("100.00"))
        # create a transaction: expense -20
        tx = Transaction.objects.create(
            account=self.acc1,
            amount=Decimal("-20.00"),
            currency="USD",
            type=Transaction.Type.EXPENSE,
            date=timezone.now().date(),
            description="Groceries",
            created_by=self.user,
        )
        # one adjustment: +5 (old 100 -> new 105) as manual correction (however delta used by model)
        adj = Adjustment.objects.create(account=self.acc1, user=self.user, old_amount=Decimal(
            "100.00"), new_amount=Decimal("105.00"), reason="Correction")
        # Compute expected balance: initial 100 + tx_sum(-20) + adjustments delta (5) => 85
        expected = Decimal("85.00")
        # get_balance uses DB aggregation
        self.assertEqual(self.acc1.get_balance(), expected)
        # recalc and create snapshot
        new_bal = self.acc1.recalculate_balance(save_snapshot=True)
        self.assertEqual(new_bal, expected)
        # cached value persisted
        self.acc1.refresh_from_db()
        self.assertEqual(self.acc1.balance, expected)
        # snapshot for today exists
        today = timezone.now().date()
        snap = BalanceSnapshot.objects.filter(
            account=self.acc1, date=today).first()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.balance, expected)

    def test_transaction_create_transfer_pair_and_balances(self):
        """
        create_transfer should create two linked transactions and update balances accordingly.
        """
        # initial: acc1 100, acc2 50
        out = Transaction.create_transfer(from_account=self.acc1, to_account=self.acc2, amount=Decimal(
            "30.00"), currency="USD", created_by=self.user)
        # related transaction should exist
        self.assertIsNotNone(out.related_transaction)
        inc = out.related_transaction
        # amounts: out negative, inc positive
        self.assertEqual(out.amount, Decimal("-30.00"))
        self.assertEqual(inc.amount, Decimal("30.00"))
        self.assertEqual(out.type, Transaction.Type.TRANSFER)
        self.assertEqual(inc.type, Transaction.Type.TRANSFER)
        # balances: acc1 = 100 - 30 = 70; acc2 = 50 + 30 = 80 (use get_balance)
        self.assertEqual(self.acc1.get_balance(), Decimal("70.00"))
        self.assertEqual(self.acc2.get_balance(), Decimal("80.00"))
        # recalc cached values
        self.acc1.recalculate_balance(save_snapshot=False)
        self.acc2.recalculate_balance(save_snapshot=False)
        self.acc1.refresh_from_db()
        self.acc2.refresh_from_db()
        self.assertEqual(self.acc1.balance, Decimal("70.00"))
        self.assertEqual(self.acc2.balance, Decimal("80.00"))

    def test_adjustment_create_and_reverse(self):
        """
        create_reverse should create a reversing adjustment and restore previous balance.
        """
        # start: acc1 balance 100
        original_balance = self.acc1.get_balance()
        # create an adjustment changing 100 -> 120
        adj = Adjustment.objects.create(account=self.acc1, user=self.user, old_amount=Decimal(
            "100.00"), new_amount=Decimal("120.00"), reason="Add funds")
        # balance now should reflect +20
        self.assertEqual(self.acc1.get_balance(), Decimal("120.00"))
        # create reverse adjustment
        rev = Adjustment.create_reverse(adj, performed_by=self.user)
        self.assertTrue(rev.is_reversal)
        # reverse should have old_amount == orig.new_amount and new_amount == orig.old_amount
        self.assertEqual(rev.old_amount, adj.new_amount)
        self.assertEqual(rev.new_amount, adj.old_amount)
        # after reversal balance should be back to original_balance (120 -> 100)
        self.assertEqual(self.acc1.get_balance(), original_balance)

    def test_audit_logs_for_transaction_and_adjustment(self):
        """
        Creating transactions and adjustments should create AuditLog entries.
        """
        before_tx_count = AuditLog.objects.count()
        tx = Transaction.objects.create(
            account=self.acc1,
            amount=Decimal("15.00"),
            currency="USD",
            type=Transaction.Type.INCOME,
            date=timezone.now().date(),
            description="Gift",
            created_by=self.user,
        )
        # There should be at least one new audit log for the transaction creation
        self.assertGreaterEqual(AuditLog.objects.count(), before_tx_count + 1)
        # check last audit corresponds to transaction create
        last = AuditLog.objects.filter(object_type="Transaction", object_id=str(
            tx.pk)).order_by("-created_at").first()
        self.assertIsNotNone(last)
        self.assertEqual(last.action, "created")
        # adjustments also produce audit
        before_adj_count = AuditLog.objects.filter(
            object_type="Adjustment").count()
        adj = Adjustment.objects.create(account=self.acc1, user=self.user,
                                        old_amount=tx.amount, new_amount=Decimal("20.00"), reason="Adjust test")
        after_adj_count = AuditLog.objects.filter(
            object_type="Adjustment").count()
        self.assertGreaterEqual(after_adj_count, before_adj_count + 1)
        a = AuditLog.objects.filter(object_type="Adjustment", object_id=str(
            adj.pk)).order_by("-created_at").first()
        self.assertIsNotNone(a)
        self.assertEqual(a.action, "created")

    def test_goal_progress_percent(self):
        """
        Goal.progress_percent returns correct percentage and handles zero target.
        """
        goal = Goal.objects.create(user=self.user, name="Vacation", target_amount=Decimal(
            "1000.00"), current_amount=Decimal("250.00"))
        self.assertEqual(goal.progress_percent(), Decimal("25.00"))
        # zero target guard
        goal_zero = Goal.objects.create(user=self.user, name="Free", target_amount=Decimal(
            "0.00"), current_amount=Decimal("0.00"))
        self.assertEqual(goal_zero.progress_percent(), Decimal("0.00"))

    def test_importjob_lifecycle_fields(self):
        """
        ImportJob basic fields and defaults.
        """
        job = ImportJob.objects.create(owner=self.user, file_name="sample.csv")
        self.assertEqual(job.status, ImportJob.Status.PENDING)
        self.assertEqual(job.rows_total, 0)
        self.assertEqual(job.rows_imported, 0)
        # updating status works
        job.status = ImportJob.Status.RUNNING
        job.save()
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.RUNNING)
