# backend/apps/finances/admin.py
"""
Admin registration for the `finances` app.

This module is written defensively: it registers ModelAdmin classes for the
most important domain models (Account, Transaction, Adjustment, Category,
Goal, ImportJob, AuditLog, BalanceSnapshot) if they exist in the app registry.
If a model is missing the import/registration is skipped silently so early
development doesn't fail.

Admin features included:
- Useful list_display / filters / search for quick management.
- Admin actions:
    * export as CSV (for accounts/transactions)
    * reverse selected adjustments (creates reverse adjustment and links them)
    * recalculate balances (if model exposes helper) — best-effort
- Read-only audit log admin
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Iterable, Optional

from django.apps import apps
from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _get_model(model_name: str):
    try:
        return apps.get_model("finances", model_name)
    except LookupError:
        return None


# Helper: safe CSV export action generator
def export_as_csv_action(description="Export selected objects as CSV", fields: Optional[Iterable[str]] = None):
    def export_action(modeladmin, request, queryset):
        model = modeladmin.model
        meta = model._meta
        header = fields or [f.name for f in meta.fields]
        # prepare CSV
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        for obj in queryset:
            row = []
            for f in header:
                val = getattr(obj, f, "")
                # Format datetimes and Decimals nicely
                if hasattr(val, "isoformat"):
                    try:
                        val = val.isoformat()
                    except Exception:
                        val = str(val)
                elif isinstance(val, Decimal):
                    val = str(val)
                row.append(val)
            writer.writerow(row)
        buffer.seek(0)
        filename = f"{meta.model_name}_export_{timezone.now().strftime('%Y%m%d%H%M%S')}.csv"
        resp = HttpResponse(buffer.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    export_action.short_description = description
    return export_action


# -------------------------
# Account admin
# -------------------------
Account = _get_model("Account")
if Account is not None:

    @admin.register(Account)
    class AccountAdmin(admin.ModelAdmin):
        list_display = ("id", "name", "owner", "type", "currency",
                        "balance_display", "threshold_notify")
        search_fields = ("name", "owner__email", "owner__username")
        list_filter = ("type", "currency")
        actions = [export_as_csv_action(
            description=_("Export selected accounts as CSV"))]

        def balance_display(self, obj):
            # Prefer a `balance` attribute or method if provided by the model
            bal = getattr(obj, "balance", None)
            if bal is None and hasattr(obj, "get_balance"):
                try:
                    bal = obj.get_balance()
                except Exception:
                    bal = None
            return bal

        balance_display.short_description = _("balance")


# -------------------------
# Transaction admin
# -------------------------
Transaction = _get_model("Transaction")
if Transaction is not None:

    @admin.register(Transaction)
    class TransactionAdmin(admin.ModelAdmin):
        list_display = ("id", "account", "amount", "currency",
                        "type", "category", "date", "is_transfer", "created_by")
        search_fields = ("description", "counterparty", "id")
        list_filter = ("type", "category", "date")
        actions = [
            export_as_csv_action(description=_(
                "Export selected transactions as CSV")),
            "action_mark_duplicates",
            "action_recalculate_balances",
        ]

        def action_mark_duplicates(self, request, queryset):
            """
            Best-effort action: mark duplicates by delegating to model method if exists,
            otherwise attempt a naive duplicate detection by identical (account, amount, date).
            """
            model = self.model
            marked = 0
            if hasattr(model, "mark_duplicates"):
                try:
                    marked = model.mark_duplicates(queryset=queryset)
                    self.message_user(request, _(
                        "Marked %d duplicates (via model method).") % marked, messages.SUCCESS)
                    return
                except Exception:
                    # fall through to naive approach
                    pass

            seen = set()
            for tx in queryset.order_by("account_id", "date", "amount"):
                key = (tx.account_id, str(tx.amount), tx.date.isoformat()
                       if hasattr(tx.date, "isoformat") else str(tx.date))
                if key in seen:
                    # attempt to set a field `is_duplicate` if exists
                    if hasattr(tx, "is_duplicate"):
                        tx.is_duplicate = True
                        tx.save(update_fields=["is_duplicate"])
                    marked += 1
                else:
                    seen.add(key)
            self.message_user(request, _(
                "Marked %d duplicates (naive).") % marked, messages.INFO)

        action_mark_duplicates.short_description = _(
            "Mark duplicate transactions")

        def action_recalculate_balances(self, request, queryset):
            """
            Best-effort: call Account.recalculate_balance() for affected accounts if available.
            """
            AccountModel = _get_model("Account")
            if AccountModel is None:
                self.message_user(request, _(
                    "Account model not found; cannot recalculate balances."), messages.WARNING)
                return

            affected_accounts = set(tx.account_id for tx in queryset if getattr(
                tx, "account_id", None) is not None)
            recalc_count = 0
            for acc_id in affected_accounts:
                acc = AccountModel.objects.filter(pk=acc_id).first()
                if acc is None:
                    continue
                if hasattr(acc, "recalculate_balance"):
                    try:
                        acc.recalculate_balance()
                        recalc_count += 1
                    except Exception:
                        continue
            self.message_user(request, _(
                "Recalculated balances for %d accounts.") % recalc_count, messages.SUCCESS)

        action_recalculate_balances.short_description = _(
            "Recalculate balances for affected accounts")


# -------------------------
# Adjustment admin
# -------------------------
Adjustment = _get_model("Adjustment")
if Adjustment is not None:

    @admin.register(Adjustment)
    class AdjustmentAdmin(admin.ModelAdmin):
        list_display = ("id", "account", "user", "old_amount",
                        "new_amount", "reason", "created_at", "reversed_by")
        search_fields = ("reason", "account__name", "user__email", "id")
        list_filter = ("created_at",)
        actions = ["action_reverse_adjustments", export_as_csv_action(
            description=_("Export selected adjustments as CSV"))]

        def action_reverse_adjustments(self, request, queryset):
            """
            Create reverse adjustments for selected adjustments:
            - swap old/new amounts
            - set reason to 'Reversal of <orig_id>: <orig_reason>'
            - link reverse via `reversed_by` or `reversal_of` if fields exist
            """
            created = 0
            model = self.model
            # Ensure atomicity
            with transaction.atomic():
                for adj in queryset.select_for_update():
                    # Skip if already reversed (best-effort detection)
                    if getattr(adj, "is_reversed", False) or getattr(adj, "reversed_by_id", None):
                        continue

                    reverse_kwargs = {}
                    # Build fields for reverse adjustment
                    old = getattr(adj, "new_amount", None)
                    new = getattr(adj, "old_amount", None)
                    reason = f"Reversal of adjustment {adj.pk}: {getattr(adj, 'reason', '')}"
                    reverse_kwargs.update(
                        {
                            "account": getattr(adj, "account", None),
                            "user": request.user,
                            "old_amount": old,
                            "new_amount": new,
                            "reason": reason,
                        }
                    )
                    # create reverse adjustment by delegating to model method if present
                    try:
                        if hasattr(model, "create_reverse"):
                            rev = model.create_reverse(
                                adj, performed_by=request.user)
                        else:
                            rev = model.objects.create(**reverse_kwargs)
                            # link original -> reversal if field exists
                            if hasattr(adj, "reversal_of") and hasattr(rev, "reversal_of"):
                                rev.reversal_of = adj
                                rev.save(update_fields=["reversal_of"])
                            if hasattr(adj, "reversed_by"):
                                adj.reversed_by = rev
                                adj.save(update_fields=["reversed_by"])
                        created += 1
                    except Exception as exc:  # pragma: no cover - defensive
                        # Log error as admin message but continue
                        self.message_user(request, _("Failed to reverse adjustment %(pk)s: %(err)s") % {
                                          "pk": adj.pk, "err": exc}, messages.ERROR)
                        continue
            self.message_user(request, _(
                "Created %d reverse adjustments.") % created, messages.SUCCESS)

        action_reverse_adjustments.short_description = _(
            "Create reverse adjustments for selected items")


# -------------------------
# Category admin (simple)
# -------------------------
Category = _get_model("Category")
if Category is not None:

    @admin.register(Category)
    class CategoryAdmin(admin.ModelAdmin):
        list_display = ("id", "name", "parent", "is_active")
        search_fields = ("name",)
        list_filter = ("is_active",)


# -------------------------
# Goal admin (simple)
# -------------------------
Goal = _get_model("Goal")
if Goal is not None:

    @admin.register(Goal)
    class GoalAdmin(admin.ModelAdmin):
        list_display = ("id", "user", "name", "target_amount",
                        "current_amount", "deadline", "is_active")
        search_fields = ("name", "user__email")
        list_filter = ("is_active",)


# -------------------------
# ImportJob admin (jobs for CSV import)
# -------------------------
ImportJob = _get_model("ImportJob")
if ImportJob is not None:

    @admin.register(ImportJob)
    class ImportJobAdmin(admin.ModelAdmin):
        list_display = ("id", "owner", "status", "created_at",
                        "completed_at", "rows_total", "rows_imported")
        search_fields = ("owner__email", "file_name")
        list_filter = ("status", "created_at")
        actions = [export_as_csv_action(description=_(
            "Export selected import jobs as CSV"))]


# -------------------------
# AuditLog admin (read-only)
# -------------------------
AuditLog = _get_model("AuditLog")
if AuditLog is not None:

    @admin.register(AuditLog)
    class AuditLogAdmin(admin.ModelAdmin):
        list_display = ("id", "object_type", "object_id",
                        "action", "actor", "created_at")
        search_fields = ("object_type", "actor__email", "object_id")
        readonly_fields = [f.name for f in AuditLog._meta.fields]
        list_filter = ("action", "object_type", "created_at")


# -------------------------
# BalanceSnapshot admin (read-only)
# -------------------------
BalanceSnapshot = _get_model("BalanceSnapshot")
if BalanceSnapshot is not None:

    @admin.register(BalanceSnapshot)
    class BalanceSnapshotAdmin(admin.ModelAdmin):
        list_display = ("id", "account", "date", "balance")
        search_fields = ("account__name",)
        list_filter = ("date",)


# If new models are added later, add registrations below following the same pattern.
