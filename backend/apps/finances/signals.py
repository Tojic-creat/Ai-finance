# backend/apps/finances/signals.py
"""
Signal handlers for the finances app.

Register by calling register_signals() from AppConfig.ready().
Handlers:
 - post_save / post_delete for Transaction and Adjustment:
   * recalc Account.cached balance (best-effort)
   * create AuditLog entries
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


def _serialize_instance(instance) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    from decimal import Decimal as _Decimal

    for f in instance._meta.fields:
        name = f.name
        try:
            val = getattr(instance, name)
            if isinstance(val, _Decimal):
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


def transaction_post_save(sender, instance, created: bool, **kwargs):
    from django.apps import apps

    Account = apps.get_model("finances", "Account")
    AuditLog = apps.get_model("finances", "AuditLog")

    # Recalculate balance best-effort
    try:
        if getattr(instance, "account", None):
            try:
                instance.account.recalculate_balance(save_snapshot=False)
            except Exception:
                acc = Account.objects.filter(pk=getattr(instance, "account_id", None)).first()
                if acc and hasattr(acc, "recalculate_balance"):
                    try:
                        acc.recalculate_balance(save_snapshot=False)
                    except Exception:
                        pass
    except Exception:
        pass

    # Create audit log (best-effort)
    try:
        AuditLog.objects.create(
            object_type="Transaction",
            object_id=str(instance.pk),
            action="created" if created else "updated",
            actor=getattr(instance, "created_by", None),
            before=None if created else {},
            after=_serialize_instance(instance),
        )
    except Exception:
        pass


def transaction_post_delete(sender, instance, **kwargs):
    from django.apps import apps

    Account = apps.get_model("finances", "Account")
    AuditLog = apps.get_model("finances", "AuditLog")

    try:
        if getattr(instance, "account", None):
            try:
                instance.account.recalculate_balance(save_snapshot=False)
            except Exception:
                acc = Account.objects.filter(pk=getattr(instance, "account_id", None)).first()
                if acc and hasattr(acc, "recalculate_balance"):
                    try:
                        acc.recalculate_balance(save_snapshot=False)
                    except Exception:
                        pass
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


def adjustment_post_save(sender, instance, created: bool, **kwargs):
    from django.apps import apps

    AuditLog = apps.get_model("finances", "AuditLog")
    Account = apps.get_model("finances", "Account")

    try:
        try:
            instance.account.recalculate_balance(save_snapshot=False)
        except Exception:
            acc = Account.objects.filter(pk=getattr(instance, "account_id", None)).first()
            if acc and hasattr(acc, "recalculate_balance"):
                try:
                    acc.recalculate_balance(save_snapshot=False)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        AuditLog.objects.create(
            object_type="Adjustment",
            object_id=str(instance.pk),
            action="created" if created else "updated",
            actor=getattr(instance, "user", None),
            before=None if created else {},
            after=_serialize_instance(instance),
            reason=getattr(instance, "reason", "") or "",
        )
    except Exception:
        pass


def adjustment_post_delete(sender, instance, **kwargs):
    from django.apps import apps

    AuditLog = apps.get_model("finances", "AuditLog")
    Account = apps.get_model("finances", "Account")

    try:
        try:
            instance.account.recalculate_balance(save_snapshot=False)
        except Exception:
            acc = Account.objects.filter(pk=getattr(instance, "account_id", None)).first()
            if acc and hasattr(acc, "recalculate_balance"):
                try:
                    acc.recalculate_balance(save_snapshot=False)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        AuditLog.objects.create(
            object_type="Adjustment",
            object_id=str(instance.pk),
            action="deleted",
            actor=getattr(instance, "user", None),
            before=_serialize_instance(instance),
            after=None,
            reason=getattr(instance, "reason", "") or "",
        )
    except Exception:
        pass


def register_signals():
    """Call this from AppConfig.ready() to attach handlers."""
    from django.apps import apps

    # Resolve model classes (ready() guarantees models are loaded)
    Transaction = apps.get_model("finances", "Transaction")
    Adjustment = apps.get_model("finances", "Adjustment")

    # Connect handlers with dispatch_uid to avoid duplicate registration
    post_save.connect(transaction_post_save, sender=Transaction, dispatch_uid="finances.transaction.post_save")
    post_delete.connect(transaction_post_delete, sender=Transaction, dispatch_uid="finances.transaction.post_delete")

    post_save.connect(adjustment_post_save, sender=Adjustment, dispatch_uid="finances.adjustment.post_save")
    post_delete.connect(adjustment_post_delete, sender=Adjustment, dispatch_uid="finances.adjustment.post_delete")
