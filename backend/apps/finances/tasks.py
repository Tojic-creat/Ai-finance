# backend/apps/finances/tasks.py
"""
Background tasks for the `finances` app.

These tasks are implemented with Celery if available. They are intentionally
defensive: if Celery or optional ML libraries are not installed, tasks will
log a helpful message and mark the ImportJob (if any) as failed (where appropriate)
instead of raising an exception that crashes the worker.

Provided tasks:
- import_csv(import_job_id): best-effort CSV import for ImportJob.file_name.
  The ImportJob model in this project stores file_name (string). This task
  expects the uploaded CSV to be available under <MEDIA_ROOT>/imports/<file_name>.
  The task does a row-count and a very small, schema-tolerant import that can be
  extended by project-specific logic later.
- recalculate_account_balance(account_id): recalculates and persists account balance.
- recalculate_all_accounts(batch_size=100): iterate accounts and recalc balances in batches.
- train_category_classifier(dry_run=True): optional placeholder task that attempts to train
  a simple classifier when scikit-learn/pandas are available. This task is illustrative
  only — real training should be done offline with careful dataset handling.
"""

from __future__ import annotations

import csv
import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

# Try to import Celery shared_task; if not available, provide a fallback decorator
try:
    from celery import shared_task  # type: ignore
except Exception:  # pragma: no cover - allows the module to be imported without Celery installed
    logger.warning(
        "Celery not installed or not available — task decorators will be no-ops.")

    def shared_task(*args, **kwargs):  # type: ignore
        """
        Fallback decorator when Celery isn't available. The decorated function
        is returned unchanged (synchronous execution).
        """
        def _decorator(fn):
            return fn
        return _decorator


# -------------------------
# Helper utilities
# -------------------------
def _import_file_path(file_name: str) -> str:
    """
    Construct expected path for an uploaded import file.
    Convention: MEDIA_ROOT/imports/<file_name>
    """
    media_root = getattr(settings, "MEDIA_ROOT", None) or os.path.join(
        settings.BASE_DIR, "media")
    return os.path.join(media_root, "imports", file_name)


# -------------------------
# Tasks
# -------------------------
@shared_task(bind=True)
def import_csv(self, import_job_id: int) -> Dict[str, Any]:
    """
    Import CSV rows for the ImportJob with id `import_job_id`.

    Behaviour (MVP / best-effort):
    - Marks job.status RUNNING -> COMPLETED/FAILED.
    - Attempts to open file at MEDIA_ROOT/imports/<file_name>.
    - Reads CSV using DictReader, counts rows and (optionally) performs lightweight
      processing. For safety, we do not create domain objects unless a clear
      mapping function is provided. This function can be extended per-project
      to map CSV columns to Transaction/Account fields and create them inside DB transactions.

    Returns a dict with summary information.
    """
    from .models import ImportJob  # local import to avoid startup import cycles

    result: Dict[str, Any] = {"import_job_id": import_job_id,
                              "status": "failed", "rows_total": 0, "rows_imported": 0}
    job = None
    try:
        job = ImportJob.objects.filter(pk=import_job_id).first()
        if job is None:
            logger.error("import_csv: ImportJob %s not found", import_job_id)
            return result

        job.status = ImportJob.Status.RUNNING
        job.save(update_fields=["status"])

        if not job.file_name:
            raise FileNotFoundError("ImportJob.file_name is empty")

        path = _import_file_path(job.file_name)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Import file not found at expected path: {path}")

        rows_total = 0
        rows_imported = 0

        # Example: open and count rows; extend this block to map rows -> Transaction/Adjustment
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            # If there are no headers, DictReader will treat first row as headers — caller must ensure template
            for row in reader:
                rows_total += 1
                # --- PLACEHOLDER: row validation & import logic ---
                # For MVP we only count rows, mark them as imported.
                # To actually import to DB, implement mapping here (careful with currencies, duplicates).
                rows_imported += 1

        # Mark job completed
        job.rows_total = rows_total
        job.rows_imported = rows_imported
        job.status = ImportJob.Status.COMPLETED
        job.completed_at = job.completed_at or getattr(
            settings, "TIME_ZONE", None)  # keep simple; DB will set real time below
        # save completed_at as now
        # placeholder; will override below
        job.completed_at = getattr(settings, "TIME_ZONE", None)
        job.completed_at = None  # we'll set with timezone.now() in atomic block

        with transaction.atomic():
            job.rows_total = rows_total
            job.rows_imported = rows_imported
            job.status = ImportJob.Status.COMPLETED
            from django.utils import timezone as _tz
            job.completed_at = _tz.now()
            job.save(update_fields=["rows_total",
                     "rows_imported", "status", "completed_at"])

        result.update({"status": "completed",
                      "rows_total": rows_total, "rows_imported": rows_imported})
        logger.info("import_csv: ImportJob %s completed: %d rows",
                    import_job_id, rows_imported)
    except Exception as exc:
        logger.exception(
            "import_csv: failed for ImportJob %s: %s", import_job_id, exc)
        if job:
            job.status = ImportJob.Status.FAILED
            # avoid excessively large error fields
            job.error = str(exc)[: 4000]
            job.save(update_fields=["status", "error"])
        result.update({"status": "failed", "error": str(exc)})
    return result


@shared_task
def recalculate_account_balance(account_id: int) -> Dict[str, Any]:
    """
    Recalculate the cached balance for a single account.
    Returns {"account_id": id, "balance": "123.45"} on success or {"error": "..."} on failure.
    """
    from .models import Account

    try:
        acc = Account.objects.get(pk=account_id)
        new_balance = acc.recalculate_balance(save_snapshot=True)
        logger.info(
            "recalculate_account_balance: account=%s new_balance=%s", account_id, new_balance)
        return {"account_id": account_id, "balance": str(new_balance)}
    except Account.DoesNotExist:
        logger.error(
            "recalculate_account_balance: account %s does not exist", account_id)
        return {"account_id": account_id, "error": "not_found"}
    except Exception as exc:
        logger.exception(
            "recalculate_account_balance: failed for account %s: %s", account_id, exc)
        return {"account_id": account_id, "error": str(exc)}


@shared_task
def recalculate_all_accounts(batch_size: int = 100) -> Dict[str, Any]:
    """
    Iterate over all accounts and recalculate balances in batches.

    Returns summary: {"processed": N, "errors": M}
    """
    from .models import Account

    processed = 0
    errors = 0
    try:
        qs = Account.objects.all().order_by("pk")
        for acc in qs.iterator(chunk_size=batch_size):
            try:
                acc.recalculate_balance(save_snapshot=True)
                processed += 1
            except Exception:
                logger.exception(
                    "recalculate_all_accounts: failed for account %s", acc.pk)
                errors += 1
    except Exception:
        logger.exception("recalculate_all_accounts: top-level failure")
        return {"processed": processed, "errors": errors, "status": "failed"}

    logger.info(
        "recalculate_all_accounts: finished processed=%d errors=%d", processed, errors)
    return {"processed": processed, "errors": errors, "status": "completed"}


@shared_task
def train_category_classifier(dry_run: bool = True) -> Dict[str, Any]:
    """
    Placeholder task to train a simple category classifier.

    Behaviour:
    - Attempts to import pandas + scikit-learn. If not available, logs and returns gracefully.
    - Loads historical transactions (only transactions with non-empty category).
    - Trains a simple TF-IDF + RandomForest pipeline mapping description/counterparty -> category.
    - Saves model to MEDIA_ROOT/ml_models/category_classifier.pkl (requires job-specific storage).
    - This task is illustrative; for production training use a dedicated ML pipeline/service.

    Returns a minimal report dict with dataset size and basic status.
    """
    try:
        import pandas as pd  # type: ignore
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        from sklearn.ensemble import RandomForestClassifier  # type: ignore
        from sklearn.pipeline import make_pipeline  # type: ignore
        import joblib  # type: ignore
    except Exception as exc:
        logger.warning(
            "train_category_classifier: required ML libs not available: %s", exc)
        return {"status": "skipped", "reason": "ml-libs-missing"}

    from .models import Transaction

    try:
        # Simple query for transactions that have categories; adjust as needed
        tx_qs = Transaction.objects.filter(category__isnull=False).values(
            "id", "description", "counterparty", "category__name")
        df = pd.DataFrame(list(tx_qs))
        if df.empty or len(df) < 50:
            logger.info(
                "train_category_classifier: not enough labeled data (%d rows)", len(df))
            return {"status": "skipped", "reason": "not_enough_data", "rows": len(df)}

        # Build text feature from description + counterparty
        df["text"] = (df.get("description", "")).fillna("") + \
            " " + (df.get("counterparty", "")).fillna("")
        X = df["text"].astype(str).values
        y = df["category__name"].astype(str).values

        pipeline = make_pipeline(TfidfVectorizer(max_features=10_000, ngram_range=(
            1, 2)), RandomForestClassifier(n_estimators=200))
        pipeline.fit(X, y)

        # Persist model
        models_dir = os.path.join(
            getattr(settings, "MEDIA_ROOT", "media"), "ml_models")
        os.makedirs(models_dir, exist_ok=True)
        model_path = os.path.join(models_dir, "category_classifier.pkl")
        joblib.dump(pipeline, model_path)

        logger.info(
            "train_category_classifier: model trained and saved to %s (rows=%d)", model_path, len(df))
        return {"status": "ok", "rows": len(df), "model_path": model_path}
    except Exception as exc:
        logger.exception("train_category_classifier: failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
