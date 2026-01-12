# backend/apps/finances/models.py
"""
Core models for the `finances` app.

Implements:
- Account: user's account/wallet with cached balance and helpers to recalculate.
- Transaction: income/expense/transfer records (transfers are paired).
- Adjustment: manual balance adjustments with reversible "reverse adjustment".
- Category: transaction categories (tree-ish).
- Goal: simple savings goal tracker.
- ImportJob: metadata for CSV import jobs.
- BalanceSnapshot: daily snapshot of account balances.
- AuditLog: simple audit trail for operations/adjustments/transactions.

Notes:
- Models include conservative, easy-to-understand business logic suitable for an MVP.
- Signal handlers at the bottom update cached balances and create audit records.
- Keep heavy logic (mass imports, complex ML categorization, advanced reconciliation)
  out of models — those belong to services/tasks.
"""

from __future__ import annotations
from django.dispatch import receiver
# imported late to avoid circulars
from django.db.models.signals import post_delete, post_save

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = get_user_model()


# -------------------------
# Helpers / validators
# -------------------------
def _max_attachment_size() -> int:
    return int(getattr(settings, "MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024))


ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "application/pdf")


def validate_attachment_file(file) -> None:
    size = getattr(file, "size", None)
    if size is not None and size > _max_attachment_size():
        raise ValidationError(_("File is too large (max %(max)s bytes)."), code="file_too_large",
                              params={"max": _max_attachment_size()})
    content_type = getattr(file, "content_type", None)
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(_("Unsupported file type."),
                              code="invalid_content_type")


# -------------------------
# Category
# -------------------------
class Category(models.Model):
    name = models.CharField(_("name"), max_length=200)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


# -------------------------
# Account
# -------------------------
class Account(models.Model):
    class Type(models.TextChoices):
        CARD = "card", _("Card")
        ACCOUNT = "account", _("Bank account")
        WALLET = "wallet", _("Wallet")
        CASH = "cash", _("Cash")

    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(_("name"), max_length=200)
    type = models.CharField(_("type"), max_length=20,
                            choices=Type.choices, default=Type.ACCOUNT)
    currency = models.CharField(_("currency"), max_length=8, default="USD")
    initial_balance = models.DecimalField(
        _("initial balance"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    threshold_notify = models.DecimalField(
        _("threshold notify"), max_digits=18, decimal_places=2, null=True, blank=True)
    tags = models.JSONField(_("tags"), default=list, blank=True)
    # Cached balance to avoid expensive aggregation on every request. Recalculated on changes.
    balance = models.DecimalField(
        _("cached balance"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Account")
        verbose_name_plural = _("Accounts")
        ordering = ("owner_id", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.owner})"

    def get_balance(self) -> Decimal:
        """
        Compute true balance from:
          initial_balance + sum(transactions for this account) + sum(adjustments delta)
        Transactions: convention - income positive, expense negative, transfers affect accordingly.
        This method performs DB aggregations and is the source of truth.
        """
        # Sum transactions amounts. Transactions have direction encoded in amount sign.
        tx_sum = (
            Transaction.objects.filter(account=self)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        # Adjustments: new_amount - old_amount summed (i.e. net effect on balance)
        adj_sum = (
            Adjustment.objects.filter(account=self)
            .annotate(delta=(F("new_amount") - F("old_amount")))
            .aggregate(total=Sum("delta"))["total"]
            or Decimal("0.00")
        )
        total = Decimal(self.initial_balance or 0) + \
            Decimal(tx_sum) + Decimal(adj_sum)
        # Normalize quantize to cents
        return Decimal(total).quantize(Decimal("0.01"))

    def recalculate_balance(self, save_snapshot: bool = True) -> Decimal:
        """
        Recalculate the cached balance and persist it. Optionally create a daily snapshot.
        Returns the recalculated balance.
        """
        with transaction.atomic():
            new_balance = self.get_balance()
            # update cached balance
            Account.objects.filter(pk=self.pk).update(
                balance=new_balance, updated_at=timezone.now())
            # refresh instance
            self.refresh_from_db(fields=["balance", "updated_at"])
            if save_snapshot:
                # create/update today's snapshot (one per day)
                today = timezone.now().date()
                BalanceSnapshot.objects.create(
                    account=self, date=today, balance=new_balance)
            return new_balance


# -------------------------
# Transaction
# -------------------------
class Transaction(models.Model):
    class Type(models.TextChoices):
        INCOME = "income", _("Income")
        EXPENSE = "expense", _("Expense")
        TRANSFER = "transfer", _("Transfer")

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="transactions")
    # amount: positive for income/transfer-in, negative for expense/transfer-out.
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    currency = models.CharField(_("currency"), max_length=8, default="USD")
    type = models.CharField(_("type"), max_length=16,
                            choices=Type.choices, default=Type.EXPENSE)
    date = models.DateField(_("date"), default=timezone.now)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    counterparty = models.CharField(
        _("counterparty"), max_length=255, blank=True)
    tags = models.JSONField(_("tags"), default=list, blank=True)
    description = models.TextField(_("description"), blank=True)
    attachment = models.FileField(_("attachment"), upload_to="transactions/",
                                  null=True, blank=True, validators=[validate_attachment_file])
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_transactions")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    # For transfers between accounts: link the counterpart transaction
    related_transaction = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="counterpart")
    is_duplicate = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ("-date", "-created_at")

    def __str__(self) -> str:
        return f"{self.type} {self.amount} {self.currency} @ {self.account}"

    @classmethod
    def create_transfer(cls, from_account: Account, to_account: Account, amount: Decimal, currency: Optional[str] = None, **kwargs) -> "Transaction":
        """
        Create a pair of transactions representing a transfer between two accounts.
        The 'from' transaction will have negative amount (outflow), the 'to' transaction positive.
        Returns the created 'from' transaction (which has related_transaction pointing to the 'to' tx).
        """
        if currency is None:
            currency = from_account.currency
        if from_account == to_account:
            raise ValueError("Cannot transfer to the same account")
        with transaction.atomic():
            # debit from_account (outflow)
            out = cls.objects.create(
                account=from_account,
                amount=(Decimal(amount) * Decimal("-1")
                        ).quantize(Decimal("0.01")),
                currency=currency,
                type=cls.Type.TRANSFER,
                **kwargs,
            )
            # credit to_account (inflow)
            inc = cls.objects.create(
                account=to_account,
                amount=Decimal(amount).quantize(Decimal("0.01")),
                currency=currency,
                type=cls.Type.TRANSFER,
                **kwargs,
            )
            # link them
            out.related_transaction = inc
            out.save(update_fields=["related_transaction"])
            inc.related_transaction = out
            inc.save(update_fields=["related_transaction"])
            return out


# -------------------------
# Adjustment
# -------------------------
class Adjustment(models.Model):
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="adjustments")
    user = models.ForeignKey(User, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="adjustments")
    old_amount = models.DecimalField(
        _("old amount"), max_digits=18, decimal_places=2)
    new_amount = models.DecimalField(
        _("new amount"), max_digits=18, decimal_places=2)
    reason = models.TextField(_("reason"))
    attachment = models.FileField(_("attachment"), upload_to="adjustments/",
                                  null=True, blank=True, validators=[validate_attachment_file])
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reversed_by")
    is_reversal = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Adjustment")
        verbose_name_plural = _("Adjustments")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Adjustment {self.pk} on {self.account} by {self.user}: {self.old_amount} -> {self.new_amount}"

    @property
    def delta(self) -> Decimal:
        return (self.new_amount - self.old_amount).quantize(Decimal("0.01"))

    @classmethod
    def create_reverse(cls, orig: "Adjustment", performed_by: Optional[User] = None, reason_prefix: Optional[str] = None) -> "Adjustment":
        """
        Create a reverse adjustment for `orig`.
        The reverse has old_amount = orig.new_amount, new_amount = orig.old_amount, linked by reversal_of.
        Returns the newly created reverse adjustment.
        """
        if orig.reversal_of or orig.is_reversal:
            raise ValueError("Original adjustment already reversed")
        with transaction.atomic():
            rev_reason = (reason_prefix or "Reversal of") + \
                f" adjustment {orig.pk}: {orig.reason}"
            rev = cls.objects.create(
                account=orig.account,
                user=performed_by,
                old_amount=orig.new_amount,
                new_amount=orig.old_amount,
                reason=rev_reason,
                is_reversal=True,
                reversal_of=orig,
            )
            # Link original to reversal (one-to-one field on orig.reversed_by)
            orig.reversal_of = orig.reversal_of  # no-op for clarity
            try:
                # set reversed_by on original via related_name 'reversed_by' exists on reversal_of field
                orig.refresh_from_db()
                orig.reversed_by = rev  # type: ignore[attr-defined]
                orig.save(update_fields=["reversed_by"])
            except Exception:
                # models may or may not have symmetric fields; ignore failures
                pass
            return rev


# -------------------------
# Goal (simple)
# -------------------------
class Goal(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="goals")
    name = models.CharField(_("name"), max_length=200)
    target_amount = models.DecimalField(
        _("target amount"), max_digits=18, decimal_places=2)
    current_amount = models.DecimalField(
        _("current amount"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    deadline = models.DateField(_("deadline"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Goal")
        verbose_name_plural = _("Goals")

    def progress_percent(self) -> Decimal:
        if self.target_amount == 0:
            return Decimal("0.00")
        return (Decimal(self.current_amount) / Decimal(self.target_amount) * Decimal("100.00")).quantize(Decimal("0.01"))


# -------------------------
# ImportJob
# -------------------------
class ImportJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    owner = models.ForeignKey(User, on_delete=models.SET_NULL,
                              null=True, blank=True, related_name="import_jobs")
    file_name = models.CharField(_("file name"), max_length=255)
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    rows_total = models.IntegerField(default=0)
    rows_imported = models.IntegerField(default=0)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    completed_at = models.DateTimeField(
        _("completed at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("Import job")
        verbose_name_plural = _("Import jobs")
        ordering = ("-created_at",)


# -------------------------
# BalanceSnapshot
# -------------------------
class BalanceSnapshot(models.Model):
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="snapshots")
    date = models.DateField(_("date"))
    balance = models.DecimalField(
        _("balance"), max_digits=18, decimal_places=2)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Balance snapshot")
        verbose_name_plural = _("Balance snapshots")
        ordering = ("-date",)
        unique_together = (("account", "date"),)

    def __str__(self) -> str:
        return f"{self.account} @ {self.date}: {self.balance}"


# -------------------------
# AuditLog
# -------------------------
class AuditLog(models.Model):
    object_type = models.CharField(_("object type"), max_length=200)
    object_id = models.CharField(_("object id"), max_length=200)
    action = models.CharField(_("action"), max_length=200)
    actor = models.ForeignKey(User, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="audit_logs")
    before = models.JSONField(_("before"), null=True, blank=True)
    after = models.JSONField(_("after"), null=True, blank=True)
    reason = models.TextField(_("reason"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Audit log")
        verbose_name_plural = _("Audit logs")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.action} on {self.object_type}/{self.object_id} by {self.actor}"


# -------------------------
# Signals: keep balance cached and create audit entries
# -------------------------

# Helper to serialize model instance into JSON-friendly dict for audit

def _serialize_instance(instance: models.Model) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for f in instance._meta.fields:
        name = f.name
        try:
            val = getattr(instance, name)
            # Convert non-serializable types
            if isinstance(val, (Decimal,)):
                val = str(val)
            elif hasattr(val, "isoformat"):
                try:
                    val = val.isoformat()
                except Exception:
                    val = str(val)
            data[name] = val
        except Exception:
            data[name] = None
    return data


@receiver(post_save, sender=Transaction)
def _transaction_post_save(sender, instance: Transaction, created: bool, **kwargs):
    """
    On transaction create/update: recalculate account balance and log audit.
    """
    try:
        instance.account.recalculate_balance(save_snapshot=False)
    except Exception:
        # Best-effort; don't crash the request
        pass

    # Audit: record create/update
    try:
        AuditLog.objects.create(
            object_type="Transaction",
            object_id=str(instance.pk),
            action="created" if created else "updated",
            actor=getattr(instance, "created_by", None),
            # detailed before-values would require tracking; leave blank for now
            before=None if created else {},
            after=_serialize_instance(instance),
        )
    except Exception:
        pass


@receiver(post_delete, sender=Transaction)
def _transaction_post_delete(sender, instance: Transaction, **kwargs):
    try:
        instance.account.recalculate_balance(save_snapshot=False)
    except Exception:
        pass
    try:
        AuditLog.objects.create(
            object_type="Transaction",
            object_id=str(instance.pk),
            action="deleted",
            actor=getattr(instance, "created_by", None),
            before=_serialize_instance(instance),
            after=None,
        )
    except Exception:
        pass


@receiver(post_save, sender=Adjustment)
def _adjustment_post_save(sender, instance: Adjustment, created: bool, **kwargs):
    """
    On adjustment create/update: recalc balance and create audit log.
    """
    try:
        instance.account.recalculate_balance(save_snapshot=False)
    except Exception:
        pass

    try:
        AuditLog.objects.create(
            object_type="Adjustment",
            object_id=str(instance.pk),
            action="created" if created else "updated",
            actor=instance.user,
            before=None if created else {},
            after=_serialize_instance(instance),
            reason=instance.reason or "",
        )
    except Exception:
        pass


@receiver(post_delete, sender=Adjustment)
def _adjustment_post_delete(sender, instance: Adjustment, **kwargs):
    try:
        instance.account.recalculate_balance(save_snapshot=False)
    except Exception:
        pass
    try:
        AuditLog.objects.create(
            object_type="Adjustment",
            object_id=str(instance.pk),
            action="deleted",
            actor=instance.user,
            before=_serialize_instance(instance),
            after=None,
            reason=instance.reason or "",
        )
    except Exception:
        pass
