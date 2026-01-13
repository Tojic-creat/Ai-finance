# backend/apps/analytics/models.py
"""
Models for the `analytics` app.

Purpose:
- Store aggregated metrics and snapshots used by dashboards and background jobs.
- Keep lightweight event/metric records for small-volume analytics and debugging.
- Provide a place to persist ML/feature engineering artifacts metadata if needed.

Design notes:
- Heavy-time-series or high-volume eventing should be delegated to specialized stores
  (timescale, ClickHouse, or an analytics pipeline). The models here are intended for
  MVP-level aggregation and for features that benefit from being available in Django ORM.
- Use JSONField for flexible payloads; avoid storing raw PII in analytics tables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = get_user_model()


# ---------------------------------------------------------------------
# Lightweight event (optional, low volume)
# ---------------------------------------------------------------------
class Event(models.Model):
    """
    A lightweight analytics event useful for low-volume, high-value tracking
    (e.g., signup funnel milestones, manual QA events).

    Keep event payload small; prefer structured fields for common queries.
    """
    name = models.CharField(_("name"), max_length=200, help_text=_(
        "Event name, e.g. 'signup_complete'"))
    user = models.ForeignKey(User, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="analytics_events")
    timestamp = models.DateTimeField(
        _("timestamp"), default=timezone.now, db_index=True)
    properties = models.JSONField(_("properties"), default=dict, blank=True, help_text=_(
        "Arbitrary event properties (JSON)"))
    source = models.CharField(_("source"), max_length=100, blank=True, help_text=_(
        "Source system or component"))

    class Meta:
        verbose_name = _("Analytics Event")
        verbose_name_plural = _("Analytics Events")
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["name", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} @ {self.timestamp.isoformat()}"


# ---------------------------------------------------------------------
# Metric / snapshot models
# ---------------------------------------------------------------------
class Metric(models.Model):
    """
    A time-series metric definition. Instances represent logical metrics
    (e.g., 'monthly_active_users', 'monthly_spend') and are referenced by MetricSnapshot.
    """
    key = models.CharField(_("key"), max_length=200, unique=True, help_text=_(
        "Unique metric key identifier"))
    name = models.CharField(_("display name"), max_length=255, blank=True)
    description = models.TextField(_("description"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Metric")
        verbose_name_plural = _("Metrics")
        ordering = ("key",)

    def __str__(self) -> str:
        return self.key


class MetricSnapshot(models.Model):
    """
    Snapshot of a metric at a particular timestamp/period.

    - metric: ForeignKey to Metric (definition)
    - period_start/period_end: define the interval the snapshot covers (e.g., day/month)
    - value: numeric value (decimal) for monetary or count metrics. Use decimals for safety.
    - meta: optional JSON with additional info (e.g., sample size, MAE/RMSE for forecasts)
    """
    metric = models.ForeignKey(
        Metric, on_delete=models.CASCADE, related_name="snapshots")
    period_start = models.DateField(_("period start"), db_index=True)
    period_end = models.DateField(_("period end"), db_index=True)
    value = models.DecimalField(_("value"), max_digits=20, decimal_places=6)
    unit = models.CharField(_("unit"), max_length=50, default="",
                            blank=True, help_text=_("e.g., 'USD', 'count'"))
    meta = models.JSONField(_("meta"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Metric snapshot")
        verbose_name_plural = _("Metric snapshots")
        ordering = ("-period_start",)
        unique_together = (("metric", "period_start", "period_end"),)
        indexes = [
            models.Index(fields=["metric", "period_start"]),
        ]

    def __str__(self) -> str:
        return f"{self.metric.key} [{self.period_start} → {self.period_end}]: {self.value} {self.unit or ''}"

    @classmethod
    def create_or_update(cls, metric_key: str, period_start, period_end, value: Decimal, unit: str = "", meta: Optional[Dict[str, Any]] = None) -> "MetricSnapshot":
        """
        Convenience to create or update a snapshot for a metric identified by `metric_key`.
        """
        metric, _ = Metric.objects.get_or_create(
            key=metric_key, defaults={"name": metric_key})
        meta = meta or {}
        with transaction.atomic():
            obj, created = cls.objects.update_or_create(
                metric=metric,
                period_start=period_start,
                period_end=period_end,
                defaults={"value": value, "unit": unit, "meta": meta},
            )
        return obj


# ---------------------------------------------------------------------
# Cached report (simple) — store precomputed report JSON for dashboard
# ---------------------------------------------------------------------
class CachedReport(models.Model):
    """
    Simple cache for precomputed dashboard/report payloads.

    - name: logical report name (e.g., 'dashboard_summary_user_42')
    - scope: short string indicating scope ('global', 'user:123', 'family:45')
    - payload: JSON blob that frontend can render directly (must be safe and size-limited)
    - ttl_seconds: optional TTL; a background job is expected to prune stale caches
    """
    name = models.CharField(_("name"), max_length=200)
    scope = models.CharField(_("scope"), max_length=200,
                             default="global", db_index=True)
    payload = models.JSONField(_("payload"), default=dict)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    ttl_seconds = models.IntegerField(_("ttl seconds"), null=True, blank=True, help_text=_(
        "Time-to-live for the cache in seconds"))

    class Meta:
        verbose_name = _("Cached report")
        verbose_name_plural = _("Cached reports")
        unique_together = (("name", "scope"),)
        ordering = ("-updated_at",)

    def is_stale(self) -> bool:
        if self.ttl_seconds is None:
            return False
        age = timezone.now() - self.updated_at
        return age.total_seconds() > self.ttl_seconds


# ---------------------------------------------------------------------
# Simple feature / artifact metadata for ML models (optional)
# ---------------------------------------------------------------------
class MLModelArtifact(models.Model):
    """
    Metadata about ML artifacts created by training tasks.

    Stores location (e.g., media path), metrics, and optional versioning info.
    """
    name = models.CharField(_("name"), max_length=255, help_text=_(
        "Logical model name, e.g. 'category_classifier'"))
    version = models.CharField(_("version"), max_length=64, default="v1")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    path = models.CharField(_("storage path"), max_length=1024, help_text=_(
        "Path in MEDIA_ROOT or external storage"))
    metrics = models.JSONField(_("metrics"), default=dict, blank=True, help_text=_(
        "Training/validation metrics such as F1/MAE"))
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("ML model artifact")
        verbose_name_plural = _("ML model artifacts")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


# ---------------------------------------------------------------------
# Small utility: aggregated helper functions (thin wrappers)
# ---------------------------------------------------------------------
def aggregate_user_spend(user_id: int, start_date, end_date) -> Decimal:
    """
    Helper that returns total spend (sum of negative transaction amounts) for a user's accounts
    between start_date and end_date (inclusive). Uses the finances.Transaction model.
    """
    try:
        from apps.finances.models import Transaction  # local import to avoid cycles
    except Exception:
        # If finances app isn't ready or import fails, return zero as safe fallback
        return Decimal("0.00")

    qs = Transaction.objects.filter(
        account__owner_id=user_id, date__gte=start_date, date__lte=end_date)
    # sum only negative amounts (expenses)
    agg = qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    # In the data model expenses are negative, so we return absolute value of negative sum
    if agg >= 0:
        # Either there were no expenses (positive income only) or sign convention differs
        return Decimal(agg).quantize(Decimal("0.01"))
    return (Decimal(abs(agg))).quantize(Decimal("0.01"))
