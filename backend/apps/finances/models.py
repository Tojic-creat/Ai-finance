# backend/apps/finances/models.py
"""
Core models for the `finances` app.

(сокращённое описание файла сохранено)
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, Sum
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
        raise ValidationError(
            _("File is too large (max %(max)s bytes)."),
            code="file_too_large",
            params={"max": _max_attachment_size()},
        )
    content_type = getattr(file, "content_type", None)
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(_("Unsupported file type."), code="invalid_content_type")


# -------------------------
# Category
# -------------------------
class Category(models.Model):
    name = models.CharField(_("name"), max_length=200)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
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

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(_("name"), max_length=200)
    type = models.CharField(_("type"), max_length=20, choices=Type.choices, default=Type.ACCOUNT)
    currency = models.CharField(_("currency"), max_length=8, default="USD")
    initial_balance = models.DecimalField(
        _("initial balance"), max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    threshold_notify = models.DecimalField(
        _("threshold notify"), max_digits=18, decimal_places=2, null=True, blank=True
    )
    tags = models.JSONField(_("tags"), default=list, blank=True)
    # Cached balance
    balance = models.DecimalField(_("cached balance"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
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
        Compute true balance from initial_balance + transactions + adjustments.
        """
        tx_sum = Transaction.objects.filter(account=self).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        adj_sum = (
            Adjustment.objects.filter(account=self)
            .annotate(delta=(F("new_amount") - F("old_amount")))
            .aggregate(total=Sum("delta"))["total"]
            or Decimal("0.00")
        )
        total = Decimal(self.initial_balance or 0) + Decimal(tx_sum) + Decimal(adj_sum)
        return Decimal(total).quantize(Decimal("0.01"))

    def recalculate_balance(self, save_snapshot: bool = True) -> Decimal:
        with transaction.atomic():
            new_balance = self.get_balance()
            Account.objects.filter(pk=self.pk).update(balance=new_balance, updated_at=timezone.now())
            self.refresh_from_db(fields=["balance", "updated_at"])
            if save_snapshot:
                today = timezone.now().date()
                # create snapshot - unique_together on (account, date) handled by model (may raise, ignore)
                try:
                    BalanceSnapshot.objects.create(account=self, date=today, balance=new_balance)
                except Exception:
                    # best-effort: ignore if snapshot cannot be created
                    pass
            return new_balance


# -------------------------
# Transaction
# -------------------------
class Transaction(models.Model):
    class Type(models.TextChoices):
        INCOME = "income", _("Income")
        EXPENSE = "expense", _("Expense")
        TRANSFER = "transfer", _("Transfer")

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="transactions")
    amount = models.DecimalField(_("amount"), max_digits=18, decimal_places=2)
    currency = models.CharField(_("currency"), max_length=8, default="USD")
    type = models.CharField(_("type"), max_length=16, choices=Type.choices, default=Type.EXPENSE)
    date = models.DateField(_("date"), default=timezone.now)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    counterparty = models.CharField(_("counterparty"), max_length=255, blank=True)
    tags = models.JSONField(_("tags"), default=list, blank=True)
    description = models.TextField(_("description"), blank=True)
    attachment = models.FileField(
        _("attachment"), upload_to="transactions/", null=True, blank=True, validators=[validate_attachment_file]
    )
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_transactions")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    related_transaction = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="counterpart")
    is_duplicate = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ("-date", "-created_at")

    def __str__(self) -> str:
        return f"{self.type} {self.amount} {self.currency} @ {self.account}"

    @property
    def is_transfer(self) -> bool:
        """
        Boolean helper used in admin list_display. True if transaction is a transfer.
        """
        return self.type == self.Type.TRANSFER

    @classmethod
    def create_transfer(
        cls, from_account: Account, to_account: Account, amount: Decimal, currency: Optional[str] = None, **kwargs
    ) -> "Transaction":
        if currency is None:
            currency = from_account.currency
        if from_account == to_account:
            raise ValueError("Cannot transfer to the same account")
        with transaction.atomic():
            out = cls.objects.create(
                account=from_account,
                amount=(Decimal(amount) * Decimal("-1")).quantize(Decimal("0.01")),
                currency=currency,
                type=cls.Type.TRANSFER,
                **kwargs,
            )
            inc = cls.objects.create(
                account=to_account,
                amount=Decimal(amount).quantize(Decimal("0.01")),
                currency=currency,
                type=cls.Type.TRANSFER,
                **kwargs,
            )
            out.related_transaction = inc
            out.save(update_fields=["related_transaction"])
            inc.related_transaction = out
            inc.save(update_fields=["related_transaction"])
            return out


# -------------------------
# Adjustment
# -------------------------
class Adjustment(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="adjustments")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="adjustments")
    old_amount = models.DecimalField(_("old amount"), max_digits=18, decimal_places=2)
    new_amount = models.DecimalField(_("new amount"), max_digits=18, decimal_places=2)
    reason = models.TextField(_("reason"))
    attachment = models.FileField(
        _("attachment"), upload_to="adjustments/", null=True, blank=True, validators=[validate_attachment_file]
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reversed_by")
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
        if orig.reversal_of or orig.is_reversal:
            raise ValueError("Original adjustment already reversed")
        with transaction.atomic():
            rev_reason = (reason_prefix or "Reversal of") + f" adjustment {orig.pk}: {orig.reason}"
            rev = cls.objects.create(
                account=orig.account,
                user=performed_by,
                old_amount=orig.new_amount,
                new_amount=orig.old_amount,
                reason=rev_reason,
                is_reversal=True,
                reversal_of=orig,
            )
            try:
                # attempt to set reverse link on original if available via related name
                orig.refresh_from_db()
                setattr(orig, "reversed_by", rev)
                orig.save(update_fields=["updated_at"])
            except Exception:
                pass
            return rev


# -------------------------
# Goal (simple)
# -------------------------
class Goal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="goals")
    name = models.CharField(_("name"), max_length=200)
    target_amount = models.DecimalField(_("target amount"), max_digits=18, decimal_places=2)
    current_amount = models.DecimalField(_("current amount"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
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

    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="import_jobs")
    file_name = models.CharField(_("file name"), max_length=255)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    rows_total = models.IntegerField(default=0)
    rows_imported = models.IntegerField(default=0)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("Import job")
        verbose_name_plural = _("Import jobs")
        ordering = ("-created_at",)


# -------------------------
# BalanceSnapshot
# -------------------------
class BalanceSnapshot(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="snapshots")
    date = models.DateField(_("date"))
    balance = models.DecimalField(_("balance"), max_digits=18, decimal_places=2)
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
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
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


# Note:
# Signal handlers (post_save/post_delete) for Transaction and Adjustment
# are intentionally placed in a separate module: apps.finances.signals
# This ensures signals are registered centrally and prevents double-registration.
#
# See apps/finances/signals.py for receiver implementations (audit + recalc).
