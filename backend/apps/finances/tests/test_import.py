# backend/apps/finances/tests/test_import.py
from __future__ import annotations

import os
import tempfile
import csv
from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings

from ..models import ImportJob
from .. import tasks


class ImportTaskTests(TestCase):
    def _write_import_file(self, media_root: str, file_name: str, rows: list[dict]):
        """
        Helper to write a CSV file under MEDIA_ROOT/imports/<file_name>
        """
        imports_dir = os.path.join(media_root, "imports")
        os.makedirs(imports_dir, exist_ok=True)
        path = os.path.join(imports_dir, file_name)
        # write CSV with DictWriter using keys from the first row or default headers
        headers = list(rows[0].keys()) if rows else ["col"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        return path

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_import_csv_success_updates_job(self):
        """
        When a valid CSV file exists, import_csv should mark ImportJob as COMPLETED
        and populate rows_total / rows_imported.
        """
        # Use a dedicated temp directory instead of global tempdir to avoid collisions
        with tempfile.TemporaryDirectory() as tmp_media:
            # override MEDIA_ROOT for this block
            with override_settings(MEDIA_ROOT=tmp_media):
                file_name = "test_import_success.csv"
                rows = [
                    {"date": "2024-01-01", "amount": "10.00", "description": "a"},
                    {"date": "2024-01-02", "amount": "20.00", "description": "b"},
                ]
                path = self._write_import_file(tmp_media, file_name, rows)
                # create job pointing to the file name
                job = ImportJob.objects.create(owner=None, file_name=file_name)
                # Call the task synchronously. In environments without Celery the decorator
                # in tasks.import_csv returns a plain function; calling it runs inline.
                try:
                    result = tasks.import_csv(job.pk)
                except TypeError:
                    # Some Celery setups expect .apply or .run signature; try fallback
                    if hasattr(tasks.import_csv, "apply"):
                        # use synchronous apply
                        result = tasks.import_csv.apply(args=(job.pk,))
                        # If apply returned an AsyncResult-like, try to get its value
                        if hasattr(result, "get"):
                            result = result.get(timeout=5)
                    elif hasattr(tasks.import_csv, "run"):
                        # If it's a bound task, call run with the task instance as self
                        result = tasks.import_csv.run(tasks.import_csv, job.pk)
                    else:
                        # last resort: call and ignore
                        result = None

                # refresh job from DB
                job.refresh_from_db()
                self.assertEqual(job.status, ImportJob.Status.COMPLETED)
                self.assertEqual(job.rows_total, 2)
                self.assertEqual(job.rows_imported, 2)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_import_csv_missing_file_marks_failed(self):
        """
        If the file referenced by ImportJob.file_name does not exist, the task should
        mark the job as FAILED and write an error message.
        """
        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media):
                missing_file = "no_such_file_{}.csv".format(os.getpid())
                job = ImportJob.objects.create(
                    owner=None, file_name=missing_file)
                # run task
                try:
                    result = tasks.import_csv(job.pk)
                except TypeError:
                    if hasattr(tasks.import_csv, "apply"):
                        result = tasks.import_csv.apply(args=(job.pk,))
                        if hasattr(result, "get"):
                            result = result.get(timeout=5)
                    elif hasattr(tasks.import_csv, "run"):
                        result = tasks.import_csv.run(tasks.import_csv, job.pk)
                    else:
                        result = None

                job.refresh_from_db()
                self.assertEqual(job.status, ImportJob.Status.FAILED)
                # error should be non-empty string
                self.assertTrue(bool(job.error))
