# backend/apps/finances/views.py
"""
DRF views / viewsets for the `finances` app.

Provides API endpoints for:
 - Account (CRUD, recalc balance)
 - Transaction (CRUD, transfers via serializer)
 - Adjustment (CRUD, reverse via model helper)
 - Goal (CRUD)
 - ImportJob (create -> enqueue import task if Celery available)
 - BalanceSnapshot (read)
 - AuditLog (read-only, admin only)

Design notes:
 - All endpoints (except AuditLog) require authentication.
 - Querysets are scoped to the requesting user's data (owner of account / related objects).
 - Heavy work (CSV import, ML tasks) is delegated to background tasks when available.
 - Views are intentionally pragmatic for an MVP and can be extended with more
   strict permission checks, pagination, throttling, and filtering.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import decorators, mixins, permissions, response, status, viewsets
from rest_framework.request import Request

from . import models, serializers

logger = logging.getLogger(__name__)


# Optional imports (filter backend, celery tasks). We use them if installed.
try:
    from django_filters.rest_framework import DjangoFilterBackend  # type: ignore
    FILTER_BACKENDS = (DjangoFilterBackend,)
except Exception:
    FILTER_BACKENDS = ()

# ===============
# Permissions
# ===============


class IsOwnerOfAccount(permissions.BasePermission):
    """
    Permission that checks if the current user is the owner of the account
    referenced in the request (useful for object-level checks).
    """

    def has_object_permission(self, request: Request, view, obj) -> bool:
        # obj may be Account or a model with account FK
        if hasattr(obj, "owner"):
            return obj.owner == request.user
        if hasattr(obj, "account"):
            return getattr(obj.account, "owner", None) == request.user
        return False


# ===============
# Account ViewSet
# ===============
class AccountViewSet(viewsets.ModelViewSet):
    """
    Manage user's accounts.

    - list: accounts owned by the user
    - create: owner is set to request.user if not provided
    - recalc_balance (POST): recalculate and return new balance
    - export (GET): export selected accounts as CSV (admin/owner)
    """
    serializer_class = serializers.AccountSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = FILTER_BACKENDS
    filterset_fields = ("currency", "type",) if FILTER_BACKENDS else ()

    def get_queryset(self):
        return models.Account.objects.filter(owner=self.request.user).order_by("name")

    def perform_create(self, serializer):
        # owner defaults to the authenticated user
        serializer.save(owner=self.request.user)

    @decorators.action(detail=True, methods=["post"], url_path="recalculate", permission_classes=(permissions.IsAuthenticated,))
    def recalculate(self, request: Request, pk=None):
        account = self.get_object()
        if account.owner != request.user:
            return response.Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        try:
            new_balance = account.recalculate_balance(save_snapshot=True)
            return response.Response({"balance": str(new_balance)}, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.exception(
                "Failed to recalculate balance for account %s: %s", account.pk, exc)
            return response.Response({"detail": "Recalculation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @decorators.action(detail=False, methods=["get"], url_path="export", permission_classes=(permissions.IsAuthenticated,))
    def export_csv(self, request: Request):
        """
        Export user's accounts as CSV. Simple export for small numbers of accounts.
        """
        qs = self.get_queryset()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        header = ["id", "name", "type", "currency",
                  "initial_balance", "balance", "owner_id"]
        writer.writerow(header)
        for acc in qs:
            writer.writerow([acc.pk, acc.name, acc.type, acc.currency, str(
                acc.initial_balance), str(acc.balance), acc.owner_id])
        buffer.seek(0)
        filename = f"accounts_export_{timezone.now().strftime('%Y%m%d%H%M%S')}.csv"
        resp = HttpResponse(buffer.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp


# ===============
# Transaction ViewSet
# ===============
class TransactionViewSet(viewsets.ModelViewSet):
    """
    Manage transactions belonging to accounts owned by the requesting user.

    Supports:
    - creating transfers via TransactionSerializer (transfer_to_account)
    - filtering by account, date range, category (when filter backend available)
    """
    serializer_class = serializers.TransactionSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = FILTER_BACKENDS
    if FILTER_BACKENDS:
        filterset_fields = {
            "account": ["exact"],
            "date": ["gte", "lte"],
            "category": ["exact"],
            "type": ["exact"],
        }

    def get_queryset(self):
        # Transactions for accounts the user owns
        return models.Transaction.objects.filter(account__owner=self.request.user).select_related("account", "category").order_by("-date", "-created_at")

    def perform_create(self, serializer):
        # created_by defaults to request.user if not provided
        serializer.save(created_by=self.request.user)

    @decorators.action(detail=False, methods=["post"], url_path="mark-duplicates", permission_classes=(permissions.IsAuthenticated,))
    def mark_duplicates(self, request: Request):
        """
        Best-effort endpoint to mark duplicates among user's transactions.
        This delegates to model logic when available; otherwise uses a naive approach.
        """
        qs = self.get_queryset()
        # Allow limiting subset via optional query params (e.g., account)
        account = request.data.get("account")
        if account:
            qs = qs.filter(account_id=account)
        marked = 0
        try:
            if hasattr(models.Transaction, "mark_duplicates"):
                marked = models.Transaction.mark_duplicates(queryset=qs)
            else:
                seen = set()
                for tx in qs.order_by("account_id", "date", "amount"):
                    key = (tx.account_id, str(tx.amount), tx.date.isoformat()
                           if hasattr(tx.date, "isoformat") else str(tx.date))
                    if key in seen:
                        tx.is_duplicate = True
                        tx.save(update_fields=["is_duplicate"])
                        marked += 1
                    else:
                        seen.add(key)
        except Exception:
            logger.exception("Error while marking duplicates")
            return response.Response({"detail": "Error while marking duplicates"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return response.Response({"marked": marked}, status=status.HTTP_200_OK)


# ===============
# Adjustment ViewSet
# ===============
class AdjustmentViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.AdjustmentSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = FILTER_BACKENDS
    filterset_fields = ("account",) if FILTER_BACKENDS else ()

    def get_queryset(self):
        # Adjustments for accounts the user owns
        return models.Adjustment.objects.filter(account__owner=self.request.user).select_related("account", "user").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @decorators.action(detail=True, methods=["post"], url_path="reverse", permission_classes=(permissions.IsAuthenticated,))
    def reverse(self, request: Request, pk=None):
        """
        Create a reverse adjustment for the given adjustment (if possible).
        """
        adj = self.get_object()
        # Only allow owner or staff to reverse
        if adj.account.owner != request.user and not request.user.is_staff:
            return response.Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        try:
            rev = models.Adjustment.create_reverse(
                adj, performed_by=request.user)
            return response.Response({"reversal_id": rev.pk}, status=status.HTTP_201_CREATED)
        except Exception as exc:
            logger.exception(
                "Failed to create reversal for adjustment %s: %s", adj.pk, exc)
            return response.Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ===============
# Goal ViewSet
# ===============
class GoalViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.GoalSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = FILTER_BACKENDS
    filterset_fields = ("is_active",) if FILTER_BACKENDS else ()

    def get_queryset(self):
        return models.Goal.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ===============
# ImportJob ViewSet
# ===============
class ImportJobViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    Create/list import jobs. Creating a job may enqueue a background Celery task if available.
    """
    serializer_class = serializers.ImportJobSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        return models.ImportJob.objects.filter(owner=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        job = serializer.save(owner=self.request.user,
                              status=models.ImportJob.Status.PENDING)
        # Try to enqueue background import task if Celery task exists
        try:
            # tasks.import_csv.delay(job.id)  # example; import task should accept job id
            from apps.finances import tasks as finances_tasks  # type: ignore

            if hasattr(finances_tasks, "import_csv"):
                finances_tasks.import_csv.delay(
                    job.id)  # type: ignore[attr-defined]
                job.status = models.ImportJob.Status.RUNNING
                job.save(update_fields=["status"])
        except Exception:
            # fail silently; job stays in PENDING and can be processed manually
            logger.debug("No import task enqueued for ImportJob %s", job.pk)


# ===============
# BalanceSnapshot ViewSet
# ===============
class BalanceSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = serializers.BalanceSnapshotSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = FILTER_BACKENDS
    if FILTER_BACKENDS:
        filterset_fields = {"account": ["exact"], "date": ["gte", "lte"]}

    def get_queryset(self):
        # snapshots for accounts the user owns
        return models.BalanceSnapshot.objects.filter(account__owner=self.request.user).select_related("account").order_by("-date")


# ===============
# AuditLog ViewSet (admin)
# ===============
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only audit logs. Admin-only access by default.
    """
    serializer_class = serializers.AuditLogSerializer
    permission_classes = (permissions.IsAdminUser,)
    filter_backends = FILTER_BACKENDS
    if FILTER_BACKENDS:
        filterset_fields = ("object_type", "actor")

    def get_queryset(self):
        return models.AuditLog.objects.all().select_related("actor").order_by("-created_at")


# End of file
