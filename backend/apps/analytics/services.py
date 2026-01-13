# backend/apps/analytics/services.py
"""
High-level services for analytics features.

Contains small, focused functions used by views, periodic jobs and the dashboard:
- aggregations over finances models (balances, spend/income)
- metric snapshotting helpers that persist MetricSnapshot entries
- dashboard payload generator (returns JSON-serializable dict)
- lightweight recommendations generator (heuristic)
- proxy to ML training task (defensive: no hard dependency on Celery/ML libs)
- CSV export helper for user reports

All functions are defensive: when encountering missing optional apps or unexpected
errors they return structured dicts with "status" and "error" where appropriate.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.db.models import F, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


def _safe_import_finances():
    """
    Safely import commonly used models from the finances app.
    Returns (Account, Transaction, Category) or raises ImportError.
    """
    try:
        from apps.finances.models import Account, Transaction, Category  # type: ignore
    except Exception as exc:
        raise ImportError(
            "Failed to import finances.models: %s" % exc) from exc
    return Account, Transaction, Category


# -------------------------
# Aggregations & summaries
# -------------------------
def get_user_accounts_summary(user_id: int, start_date, end_date) -> Dict[str, Any]:
    """
    Return summary for a user's accounts between start_date and end_date (inclusive).
    Fields returned:
      - total_balance: Decimal (sum of account.get_balance())
      - total_income: Decimal (sum of positive transaction amounts in period)
      - total_expense: Decimal (absolute sum of negative transaction amounts in period)
      - tx_count: int
      - by_account: list of {account_id, name, balance}
    """
    try:
        Account, Transaction, Category = _safe_import_finances()
    except ImportError as exc:
        logger.debug("get_user_accounts_summary: %s", exc)
        return {"status": "error", "error": str(exc)}

    # total balance: use Account.get_balance if available or sum of cached balance/initial_balance
    accounts = Account.objects.filter(owner_id=user_id)
    total_balance = Decimal("0.00")
    by_account: List[Dict[str, Any]] = []
    for acc in accounts:
        try:
            bal = acc.get_balance()
        except Exception:
            # fallback to cached field if present
            bal = getattr(acc, "balance", Decimal("0.00")) or Decimal("0.00")
        bal = Decimal(bal).quantize(Decimal("0.01"))
        total_balance += bal
        by_account.append({"account_id": acc.pk, "name": acc.name,
                          "balance": bal, "currency": getattr(acc, "currency", None)})

    # transactions aggregation
    tx_qs = Transaction.objects.filter(
        account__owner_id=user_id, date__gte=start_date, date__lte=end_date)
    agg = tx_qs.aggregate(total=Sum("amount"), positive=Sum("amount", filter=Q(
        amount__gt=0)), negative=Sum("amount", filter=Q(amount__lt=0)))
    total_income = Decimal(agg.get("positive") or 0).quantize(Decimal("0.01"))
    total_negative = Decimal(
        agg.get("negative") or 0).quantize(Decimal("0.01"))
    total_expense = abs(total_negative)
    tx_count = tx_qs.count()

    return {
        "status": "ok",
        "total_balance": total_balance.quantize(Decimal("0.01")),
        "total_income": total_income,
        "total_expense": total_expense,
        "tx_count": tx_count,
        "by_account": by_account,
    }


def top_categories(user_id: int, start_date, end_date, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Return top categories by absolute spend (descending) for given user and period.
    Returns list of {category: name, total: Decimal}.
    """
    try:
        Account, Transaction, Category = _safe_import_finances()
    except ImportError as exc:
        logger.debug("top_categories: %s", exc)
        return []

    qs = Transaction.objects.filter(
        account__owner_id=user_id, date__gte=start_date, date__lte=end_date, category__isnull=False)
    # sum amounts by category (transactions may be positive/negative; we treat expenses as abs(negative sums))
    # We'll aggregate by category__name for simplicity.
    rows = (
        qs.values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")[: limit]
    )
    out = []
    for r in rows:
        name = r.get("category__name") or "Uncategorized"
        total = Decimal(r.get("total") or 0).quantize(Decimal("0.01"))
        out.append({"category": name, "total": total})
    return out


# -------------------------
# Metric snapshot helpers
# -------------------------
def create_metric_snapshot(metric_key: str, period_start, period_end, compute_value_callable, unit: str = "", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Compute a metric using compute_value_callable() and persist a MetricSnapshot.

    compute_value_callable: zero-arg callable returning Decimal or numeric.
    Returns dict with snapshot info or error.
    """
    try:
        from .models import MetricSnapshot  # local import
    except Exception as exc:
        logger.exception(
            "create_metric_snapshot: analytics.models not available: %s", exc)
        return {"status": "error", "error": "analytics.models unavailable", "exc": str(exc)}

    try:
        val = compute_value_callable()
        val_dec = Decimal(str(val)).quantize(Decimal("0.01"))
        snap = MetricSnapshot.create_or_update(
            metric_key, period_start, period_end, val_dec, unit=unit, meta=meta or {})
        return {"status": "ok", "snapshot_id": snap.pk, "value": str(snap.value)}
    except Exception as exc:
        logger.exception(
            "create_metric_snapshot failed for %s: %s", metric_key, exc)
        return {"status": "error", "error": str(exc)}


# -------------------------
# Dashboard payload
# -------------------------
def generate_user_dashboard(user_id: int) -> Dict[str, Any]:
    """
    Compose a dashboard payload for a given user.

    Payload example:
    {
      "summary": {...},  # see get_user_accounts_summary for keys
      "top_categories": [...],
      "recent_transactions": [...],  # lightweight list
      "recommendations": [...],
    }
    """
    now = timezone.now().date()
    start_30 = now - timezone.timedelta(days=30)

    try:
        Account, Transaction, Category = _safe_import_finances()
    except ImportError as exc:
        return {"status": "error", "error": str(exc)}

    try:
        summary = get_user_accounts_summary(user_id, start_30, now)
        # get top categories for last 30 days
        categories = top_categories(user_id, start_30, now, limit=6)

        # recent transactions: last 10
        txs_qs = Transaction.objects.filter(account__owner_id=user_id).select_related(
            "category", "account").order_by("-date", "-created_at")[:10]
        recent = []
        for tx in txs_qs:
            recent.append(
                {
                    "id": tx.pk,
                    "account_id": tx.account_id,
                    "account_name": getattr(tx.account, "name", None),
                    "amount": str(Decimal(tx.amount).quantize(Decimal("0.01"))),
                    "currency": getattr(tx, "currency", None),
                    "type": getattr(tx, "type", None),
                    "date": tx.date.isoformat() if hasattr(tx, "date") else None,
                    "category": getattr(tx.category, "name", None) if getattr(tx, "category", None) else None,
                    "description": getattr(tx, "description", None),
                }
            )

        recommendations = generate_basic_recommendations(user_id)

        payload = {
            "status": "ok",
            "summary": summary,
            "top_categories": categories,
            "recent_transactions": recent,
            "recommendations": recommendations,
            "generated_at": timezone.now().isoformat(),
        }
        return payload
    except Exception as exc:
        logger.exception(
            "generate_user_dashboard failed for user %s: %s", user_id, exc)
        return {"status": "error", "error": str(exc)}


# -------------------------
# Recommendations (simple heuristics)
# -------------------------
def generate_basic_recommendations(user_id: int) -> List[Dict[str, Any]]:
    """
    Generate a short list of heuristic recommendations, used before ML recommendations are available.

    Examples:
    - If average monthly expense (last 3 months) > X, suggest saving Y%
    - If no emergency fund (savings account balance < 1 * monthly expenses), suggest building emergency fund
    """
    try:
        Account, Transaction, Category = _safe_import_finances()
    except ImportError as exc:
        logger.debug("generate_basic_recommendations: %s", exc)
        return []

    now = timezone.now().date()
    # compute last 3 full months boundaries
    end = now
    start = end - timezone.timedelta(days=90)
    qs = Transaction.objects.filter(
        account__owner_id=user_id, date__gte=start, date__lte=end)
    agg = qs.aggregate(total=Sum("amount"))
    total = Decimal(agg.get("total") or 0).quantize(Decimal("0.01"))
    # expenses are negative conventionally
    monthly_avg_expense = (abs(
        total) / Decimal(3)).quantize(Decimal("0.01")) if total != 0 else Decimal("0.00")

    recommendations: List[Dict[str, Any]] = []
    # simple thresholds from settings or defaults
    try:
        min_emergency_months = int(
            getattr(__import__("django.conf").conf.settings, "EMERGENCY_FUND_MONTHS", 3))
    except Exception:
        min_emergency_months = 3

    # total savings balance: sum of accounts with tag 'savings' or type 'savings'
    savings_accounts = Account.objects.filter(owner_id=user_id).filter(
        Q(type__icontains="savings") | Q(tags__icontains="savings"))
    total_savings = Decimal("0.00")
    for acc in savings_accounts:
        try:
            total_savings += Decimal(acc.get_balance()
                                     ).quantize(Decimal("0.01"))
        except Exception:
            total_savings += Decimal(getattr(acc, "balance", 0) or 0)

    # Recommendation 1: build emergency fund if savings < monthly_avg * min_emergency_months
    required = (monthly_avg_expense * Decimal(min_emergency_months)
                ).quantize(Decimal("0.01"))
    if required > Decimal("0.00") and total_savings < required:
        deficit = (required - total_savings).quantize(Decimal("0.01"))
        recommendations.append(
            {
                "id": "emergency_fund",
                "title": "Build emergency fund",
                "explanation": f"Your estimated emergency fund target is {required}. Current savings: {total_savings}. You are short by {deficit}.",
                "suggested_action": f"Try to save { (deficit / Decimal(3)).quantize(Decimal('0.01')) } per month for 3 months",
                "confidence": 0.6,
            }
        )

    # Recommendation 2: reduce top spending category by 10% to save estimated amount
    top = top_categories(user_id, start, end, limit=1)
    if top:
        cat = top[0]
        estimated_saving = (cat["total"] * Decimal("0.10")
                            ).quantize(Decimal("0.01"))
        if estimated_saving > Decimal("0.00"):
            recommendations.append(
                {
                    "id": "reduce_top_category",
                    "title": f"Reduce spending in {cat['category']}",
                    "explanation": f"Reducing {cat['category']} by 10% could save approximately {estimated_saving}.",
                    "suggested_action": "Review subscriptions and recurring expenses in this category.",
                    "confidence": 0.55,
                }
            )

    return recommendations


# -------------------------
# ML proxy / training
# -------------------------
def train_category_classifier_async(dry_run: bool = True) -> Dict[str, Any]:
    """
    Try to trigger the ML training task from finances.tasks.train_category_classifier.

    Returns a dict with task info or error. This function is defensive: if Celery is not configured
    or task not found it returns a helpful status.
    """
    try:
        from apps.finances import tasks as finances_tasks  # type: ignore
    except Exception as exc:
        logger.debug(
            "train_category_classifier_async: finances.tasks import failed: %s", exc)
        return {"status": "error", "error": "finances.tasks not available", "exc": str(exc)}

    task = getattr(finances_tasks, "train_category_classifier", None)
    if task is None:
        return {"status": "error", "error": "train_category_classifier task not found"}

    try:
        # If task is a Celery task, calling .delay will enqueue; if it's a plain function, call synchronously
        if hasattr(task, "delay"):
            async_result = task.delay(dry_run)
            return {"status": "enqueued", "task_id": getattr(async_result, "id", None)}
        else:
            # synchronous run
            result = task(dry_run)
            return {"status": "ran", "result": result}
    except Exception as exc:
        logger.exception("train_category_classifier_async failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# -------------------------
# CSV export helper
# -------------------------
def export_user_transactions_csv(user_id: int, start_date, end_date) -> Tuple[bytes, str]:
    """
    Export user transactions to CSV for the given period.

    Returns (bytes_content, filename). On error raises Exception.
    """
    Account, Transaction, Category = _safe_import_finances()
    qs = Transaction.objects.filter(account__owner_id=user_id, date__gte=start_date,
                                    date__lte=end_date).select_related("account", "category").order_by("date")
    buffer = io.StringIO()
    fieldnames = ["id", "date", "account", "amount", "currency",
                  "type", "category", "counterparty", "description"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for tx in qs:
        writer.writerow(
            {
                "id": tx.pk,
                "date": getattr(tx, "date", None).isoformat() if getattr(tx, "date", None) else "",
                "account": getattr(tx.account, "name", None) if getattr(tx, "account", None) else "",
                "amount": str(Decimal(tx.amount).quantize(Decimal("0.01"))),
                "currency": getattr(tx, "currency", ""),
                "type": getattr(tx, "type", ""),
                "category": getattr(tx.category, "name", "") if getattr(tx, "category", None) else "",
                "counterparty": getattr(tx, "counterparty", "") or "",
                "description": getattr(tx, "description", "") or "",
            }
        )
    content = buffer.getvalue().encode("utf-8")
    filename = f"user_{user_id}_transactions_{start_date.isoformat()}_{end_date.isoformat()}.csv"
    return content, filename
