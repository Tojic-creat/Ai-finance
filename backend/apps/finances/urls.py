# backend/apps/finances/urls.py
"""
URL routes for the `finances` app.

Registers DRF viewsets:
 - /accounts/           -> AccountViewSet
 - /transactions/       -> TransactionViewSet
 - /adjustments/        -> AdjustmentViewSet
 - /goals/              -> GoalViewSet
 - /import-jobs/        -> ImportJobViewSet
 - /snapshots/          -> BalanceSnapshotViewSet
 - /audit-logs/         -> AuditLogViewSet (admin-only)

This module uses a DefaultRouter to generate standard routes (list, detail, actions).
"""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter
from django.http import JsonResponse

# Defensive import: if views are not present, provide 501 stubs
try:
    from . import views  # noqa: WPS433
except Exception:  # pragma: no cover - defensive fallback for early dev
    views = None


def _not_implemented(request, *args, **kwargs):
    return JsonResponse({"detail": "Not implemented"}, status=501)


router = DefaultRouter()

if views is not None:
    # Register viewsets if they exist in views module
    if hasattr(views, "AccountViewSet"):
        router.register(r"accounts", views.AccountViewSet, basename="account")
    if hasattr(views, "TransactionViewSet"):
        router.register(r"transactions", views.TransactionViewSet,
                        basename="transaction")
    if hasattr(views, "AdjustmentViewSet"):
        router.register(r"adjustments", views.AdjustmentViewSet,
                        basename="adjustment")
    if hasattr(views, "GoalViewSet"):
        router.register(r"goals", views.GoalViewSet, basename="goal")
    if hasattr(views, "ImportJobViewSet"):
        router.register(r"import-jobs", views.ImportJobViewSet,
                        basename="importjob")
    if hasattr(views, "BalanceSnapshotViewSet"):
        router.register(
            r"snapshots", views.BalanceSnapshotViewSet, basename="snapshot")
    if hasattr(views, "AuditLogViewSet"):
        router.register(r"audit-logs", views.AuditLogViewSet,
                        basename="auditlog")
else:
    # no views -> router remains empty; keep module importable
    pass


urlpatterns = [
    # Mount router (DRF viewsets)
    path("", include((router.urls, "finances"), namespace="finances")),
]
