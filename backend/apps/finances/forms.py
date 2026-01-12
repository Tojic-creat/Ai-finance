# backend/apps/finances/forms.py
"""
Forms for the `finances` app.

Provides:
- AccountForm          : create / edit account
- TransactionForm      : create / edit transaction (validates currency/account consistency)
- AdjustmentForm       : manual balance adjustments (reason required, file validation)
- ImportCSVForm        : upload CSV for import + preview row count
- GoalForm             : create / edit savings goal
- BalanceFilterForm    : simple filter form for reports / snapshots

Forms reuse model validators (e.g. validate_attachment_file) and are safe for use
in Django templates or in views.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from .models import (
    Account,
    Adjustment,
    Category,
    Goal,
    Transaction,
    validate_attachment_file,
)


# -------------------------
# Attachment field wrapper
# -------------------------
class AttachmentField(forms.FileField):
    default_validators = [validate_attachment_file]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)


# -------------------------
# Account form
# -------------------------
class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ("name", "type", "currency",
                  "initial_balance", "threshold_notify", "tags")
        widgets = {
            "tags": forms.TextInput(attrs={"placeholder": '["savings","emergency"]'}),
        }
        help_texts = {
            "initial_balance": _("Initial balance used as a starting point for balance calculations."),
            "threshold_notify": _("Notify when cached balance goes below this amount."),
        }

    def clean_initial_balance(self):
        val = self.cleaned_data.get("initial_balance")
        if val is None:
            return Decimal("0.00")
        return Decimal(val).quantize(Decimal("0.01"))


# -------------------------
# Transaction form
# -------------------------
class TransactionForm(forms.ModelForm):
    # optional convenience field for creating a transfer: destination account id
    transfer_to_account = forms.ModelChoiceField(
        queryset=Account.objects.all(),
        required=False,
        help_text=_(
            "If provided and type=transfer, a paired transaction will be created to this account."),
    )

    attachment = AttachmentField()

    class Meta:
        model = Transaction
        fields = (
            "account",
            "amount",
            "currency",
            "type",
            "date",
            "category",
            "counterparty",
            "tags",
            "description",
            "attachment",
        )
        widgets = {"date": forms.DateInput(
            attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean_amount(self):
        amt = self.cleaned_data.get("amount")
        try:
            dec = Decimal(amt)
        except Exception:
            raise ValidationError(_("Invalid amount format."))
        if dec == Decimal("0"):
            raise ValidationError(_("Amount must be non-zero."))
        return dec.quantize(Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        account: Optional[Account] = cleaned.get("account")
        currency = cleaned.get("currency")
        tx_type = cleaned.get("type")
        dest = cleaned.get("transfer_to_account")

        if account and currency and account.currency and currency != account.currency:
            raise ValidationError(
                {
                    "currency": _(
                        "Transaction currency (%(tx_cur)s) does not match account currency (%(acc_cur)s). "
                        "Please convert or use an account with the matching currency."
                    )
                    % {"tx_cur": currency, "acc_cur": account.currency}
                }
            )

        if tx_type == Transaction.Type.TRANSFER:
            if dest is None:
                raise ValidationError({"transfer_to_account": _(
                    "Destination account is required for transfers.")})
            if account and dest and account.pk == dest.pk:
                raise ValidationError({"transfer_to_account": _(
                    "Destination account must be different from source account.")})

        return cleaned


# -------------------------
# Adjustment form
# -------------------------
class AdjustmentForm(forms.ModelForm):
    attachment = AttachmentField()
    # optional flag to skip strict old_amount check (admin overrides)
    force = forms.BooleanField(required=False, initial=False, help_text=_(
        "Force adjustment even if old amount doesn't match current account balance."))

    class Meta:
        model = Adjustment
        fields = ("account", "old_amount", "new_amount",
                  "reason", "attachment", "force")

    def clean_reason(self):
        r = self.cleaned_data.get("reason")
        if not r or str(r).strip() == "":
            raise ValidationError(
                _("Reason is required for adjustments."), code="required")
        return r

    def clean_old_amount(self):
        val = self.cleaned_data.get("old_amount")
        try:
            return Decimal(val).quantize(Decimal("0.01"))
        except Exception:
            raise ValidationError(_("Invalid decimal value for old amount."))

    def clean_new_amount(self):
        val = self.cleaned_data.get("new_amount")
        try:
            return Decimal(val).quantize(Decimal("0.01"))
        except Exception:
            raise ValidationError(_("Invalid decimal value for new amount."))

    def clean(self):
        cleaned = super().clean()
        account: Optional[Account] = cleaned.get("account")
        old = cleaned.get("old_amount")
        new = cleaned.get("new_amount")
        force = cleaned.get("force", False)

        if account is None:
            raise ValidationError({"account": _("Account is required.")})

        # If account provided and not forced, verify old_amount matches cached balance (best-effort)
        if not force and old is not None:
            try:
                if account.balance != old:
                    raise ValidationError(
                        {
                            "old_amount": _(
                                "Old amount (%(old)s) does not match account cached balance (%(bal)s). "
                                "If you are sure, check 'Force' to override."
                            )
                            % {"old": old, "bal": account.balance}
                        }
                    )
            except Exception:
                # If balance not available for some reason, skip strict check
                pass

        # new/old presence validated by field-level clean, but double-check
        if old is None or new is None:
            raise ValidationError(
                _("Both old_amount and new_amount are required."))

        return cleaned


# -------------------------
# Import CSV form
# -------------------------
class ImportCSVForm(forms.Form):
    csv_file = forms.FileField(label=_("CSV file"), help_text=_(
        "Upload CSV using the project's template."))
    preview_rows = forms.IntegerField(
        label=_("Preview rows"), required=False, min_value=1, max_value=100, initial=10)

    def clean_csv_file(self):
        f = self.cleaned_data.get("csv_file")
        if not f:
            raise ValidationError(_("CSV file is required."))
        # Basic content-type heuristic (not strict)
        content_type = getattr(f, "content_type", "")
        if content_type and "csv" not in content_type and "text" not in content_type:
            # allow but warn: not raising to be permissive
            raise ValidationError(
                _("Uploaded file does not look like a CSV (content-type=%s).") % content_type)
        return f


# -------------------------
# Goal form
# -------------------------
class GoalForm(forms.ModelForm):
    class Meta:
        model = Goal
        fields = ("name", "target_amount",
                  "current_amount", "deadline", "is_active")
        widgets = {"deadline": forms.DateInput(attrs={"type": "date"})}

    def clean_target_amount(self):
        try:
            val = Decimal(self.cleaned_data.get("target_amount"))
        except Exception:
            raise ValidationError(_("Invalid target amount."))
        if val <= 0:
            raise ValidationError(_("Target amount must be positive."))
        return val.quantize(Decimal("0.01"))


# -------------------------
# Balance filter / snapshot form
# -------------------------
class BalanceFilterForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.all(), required=False)
    date_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        dfrom = cleaned.get("date_from")
        dto = cleaned.get("date_to")
        if dfrom and dto and dfrom > dto:
            raise ValidationError(
                {"date_to": _("date_to must be after date_from")})
        # default range to last 30 days if none provided
        if not dfrom and not dto:
            cleaned["date_to"] = timezone.now().date()
            cleaned["date_from"] = cleaned["date_to"] - \
                timezone.timedelta(days=30)
        return cleaned
