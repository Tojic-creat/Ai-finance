# backend/apps/finances/serializers.py
"""
DRF serializers for the `finances` app.

Serializers:
- CategorySerializer
- AccountSerializer
- TransactionSerializer (supports transfer creation via `transfer_to_account` write-only field)
- AdjustmentSerializer
- GoalSerializer
- ImportJobSerializer
- BalanceSnapshotSerializer
- AuditLogSerializer

Notes:
- Many fields are read_only where appropriate.
- For transfers: if `type == "transfer"` and `transfer_to_account` is provided,
  TransactionSerializer will create a paired transfer using Transaction.create_transfer.
- Currency mismatch between transaction.currency and account.currency raises a ValidationError
  (the UI/backend should offer a currency conversion flow if needed).
- `created_by` is auto-populated from `context['request'].user` when available.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Account,
    Adjustment,
    AuditLog,
    BalanceSnapshot,
    Category,
    Goal,
    ImportJob,
    Transaction,
)
from .models import validate_attachment_file  # reuse the model validator

User = get_user_model()


# -------------------------
# Basic serializers
# -------------------------
class SimpleUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", getattr(User, "USERNAME_FIELD", "username"), "email")
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "parent", "is_active")
        read_only_fields = ("id",)


# -------------------------
# Account serializer
# -------------------------
class AccountSerializer(serializers.ModelSerializer):
    owner = SimpleUserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="owner", queryset=User.objects.all(), required=False
    )
    balance = serializers.DecimalField(
        read_only=True, max_digits=18, decimal_places=2)

    class Meta:
        model = Account
        fields = (
            "id",
            "owner",
            "owner_id",
            "name",
            "type",
            "currency",
            "initial_balance",
            "threshold_notify",
            "tags",
            "balance",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "balance")

    def create(self, validated_data: Dict[str, Any]) -> Account:
        # owner comes from owner field if provided, otherwise from request user
        owner = validated_data.pop("owner", None)
        request = self.context.get("request")
        if owner is None and request and hasattr(request, "user") and request.user.is_authenticated:
            owner = request.user
        if owner is None:
            raise serializers.ValidationError({"owner": "Owner is required"})
        account = Account.objects.create(owner=owner, **validated_data)
        # initial recalc to set cached balance (signals may also handle it)
        try:
            account.recalculate_balance(save_snapshot=False)
        except Exception:
            pass
        return account


# -------------------------
# Transaction serializer
# -------------------------
class TransactionSerializer(serializers.ModelSerializer):
    account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all())
    account_detail = AccountSerializer(source="account", read_only=True)

    created_by = SimpleUserSerializer(read_only=True)
    created_by_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="created_by", queryset=User.objects.all(), required=False
    )

    # For transfers: write-only field specifying destination account id.
    transfer_to_account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(), write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Transaction
        fields = (
            "id",
            "account",
            "account_detail",
            "amount",
            "currency",
            "type",
            "date",
            "category",
            "counterparty",
            "tags",
            "description",
            "attachment",
            "created_by",
            "created_by_id",
            "related_transaction",
            "transfer_to_account",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "account_detail", "created_by",
                            "related_transaction", "created_at", "updated_at")

    def validate_attachment(self, file):
        # reuse model validator
        try:
            validate_attachment_file(file)
        except Exception as exc:
            raise serializers.ValidationError(str(exc))
        return file

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        - Ensure currency matches account currency (unless explicit conversion flow is used).
        - If type is transfer and transfer_to_account is provided, ensure accounts differ.
        - Ensure amount is non-zero.
        """
        account: Optional[Account] = attrs.get(
            "account") or getattr(self.instance, "account", None)
        currency = attrs.get("currency") or getattr(
            self.instance, "currency", None)
        tx_type = attrs.get("type") or getattr(self.instance, "type", None)
        amount = attrs.get("amount") or getattr(self.instance, "amount", None)
        dest_account = attrs.get("transfer_to_account")

        if amount is None:
            raise serializers.ValidationError(
                {"amount": "Amount is required."})
        try:
            dec_amount = Decimal(amount)
        except Exception:
            raise serializers.ValidationError(
                {"amount": "Invalid decimal amount."})
        if dec_amount == Decimal("0"):
            raise serializers.ValidationError(
                {"amount": "Amount must be non-zero."})

        if account and currency and account.currency and currency != account.currency:
            # For MVP: require currency match. In the future we can accept conversion payload.
            raise serializers.ValidationError(
                {
                    "currency": f"Transaction currency ({currency}) does not match account currency ({account.currency}). "
                    "Please convert the amount before creating the transaction or use the currency-conversion endpoint."
                }
            )

        if tx_type == Transaction.Type.TRANSFER:
            if dest_account is None:
                raise serializers.ValidationError(
                    {"transfer_to_account": "transfer_to_account is required for transfer type."})
            if account and dest_account and account.pk == dest_account.pk:
                raise serializers.ValidationError(
                    {"transfer_to_account": "Transfer destination must be a different account."})

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Transaction:
        # Pop transfer_to_account if present
        dest_account = validated_data.pop("transfer_to_account", None)
        # created_by: prefer request.user
        request = self.context.get("request")
        if "created_by" not in validated_data:
            if request and hasattr(request, "user") and request.user.is_authenticated:
                validated_data["created_by"] = request.user

        # If transaction is a transfer and destination provided -> create paired transfer transaction
        if validated_data.get("type") == Transaction.Type.TRANSFER and dest_account is not None:
            # amount in create_transfer should be positive; Transaction.create_transfer will create negative outflow for source
            amount = validated_data.pop("amount")
            currency = validated_data.get("currency", None)
            # collect extra kwargs to pass to both transaction creations
            extra = {
                "date": validated_data.get("date", timezone.now().date()),
                "category": validated_data.get("category", None),
                "counterparty": validated_data.get("counterparty", ""),
                "tags": validated_data.get("tags", []),
                "description": validated_data.get("description", ""),
                "created_by": validated_data.get("created_by", None),
            }
            # Use model classmethod to create pair atomically
            with db_transaction.atomic():
                out_tx = Transaction.create_transfer(
                    from_account=validated_data["account"],
                    to_account=dest_account,
                    amount=Decimal(amount),
                    currency=currency,
                    **extra,
                )
                # refresh and return the 'out' transaction (already saved)
                return out_tx
        # Normal transaction create
        tx = Transaction.objects.create(**validated_data)
        return tx


# -------------------------
# Adjustment serializer
# -------------------------
class AdjustmentSerializer(serializers.ModelSerializer):
    account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all())
    user = SimpleUserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="user", queryset=User.objects.all(), required=False)

    class Meta:
        model = Adjustment
        fields = (
            "id",
            "account",
            "user",
            "user_id",
            "old_amount",
            "new_amount",
            "reason",
            "attachment",
            "created_at",
        )
        read_only_fields = ("id", "created_at", "user")

    def validate_attachment(self, file):
        try:
            validate_attachment_file(file)
        except Exception as exc:
            raise serializers.ValidationError(str(exc))
        return file

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate that reason is present and amounts make sense.
        """
        old = attrs.get("old_amount")
        new = attrs.get("new_amount")
        reason = attrs.get("reason") or getattr(self.instance, "reason", None)
        if reason in (None, ""):
            raise serializers.ValidationError(
                {"reason": "Reason is required for adjustments."})
        if old is None or new is None:
            raise serializers.ValidationError(
                {"amounts": "Both old_amount and new_amount are required."})
        # allow old == new but it's probably a no-op; we still allow it
        return attrs

    def create(self, validated_data: Dict[str, Any]) -> Adjustment:
        # set user from request if not provided
        request = self.context.get("request")
        if "user" not in validated_data:
            if request and hasattr(request, "user") and request.user.is_authenticated:
                validated_data["user"] = request.user
        adj = Adjustment.objects.create(**validated_data)
        return adj


# -------------------------
# Goal, ImportJob, BalanceSnapshot, AuditLog serializers (read-friendly)
# -------------------------
class GoalSerializer(serializers.ModelSerializer):
    user = SimpleUserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="user", queryset=User.objects.all(), required=False)

    class Meta:
        model = Goal
        fields = ("id", "user", "user_id", "name", "target_amount",
                  "current_amount", "deadline", "is_active", "created_at")
        read_only_fields = ("id", "created_at",)


class ImportJobSerializer(serializers.ModelSerializer):
    owner = SimpleUserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="owner", queryset=User.objects.all(), required=False)

    class Meta:
        model = ImportJob
        fields = ("id", "owner", "owner_id", "file_name", "status",
                  "rows_total", "rows_imported", "created_at", "completed_at", "error")
        read_only_fields = ("id", "status", "rows_total",
                            "rows_imported", "created_at", "completed_at", "error")


class BalanceSnapshotSerializer(serializers.ModelSerializer):
    account = AccountSerializer(read_only=True)
    account_id = serializers.PrimaryKeyRelatedField(
        write_only=True, source="account", queryset=Account.objects.all())

    class Meta:
        model = BalanceSnapshot
        fields = ("id", "account", "account_id",
                  "date", "balance", "created_at")
        read_only_fields = ("id", "created_at")


class AuditLogSerializer(serializers.ModelSerializer):
    actor = SimpleUserSerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = ("id", "object_type", "object_id", "action",
                  "actor", "before", "after", "reason", "created_at")
        read_only_fields = fields


# End of file
