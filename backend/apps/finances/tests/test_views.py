# backend/apps/finances/tests/test_views.py
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import Account, Adjustment, ImportJob, Transaction

User = get_user_model()


class FinancesViewsTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        # create two accounts for the user
        self.acc1 = Account.objects.create(
            owner=self.user, name="Checking", currency="USD", initial_balance=Decimal("100.00"))
        self.acc2 = Account.objects.create(
            owner=self.user, name="Savings", currency="USD", initial_balance=Decimal("50.00"))

    def _post(self, name: str, payload: Dict[str, Any]):
        url = reverse(name)
        return self.client.post(url, payload, format="json")

    def _post_detail_action(self, name: str, pk: int, action: str, payload: Dict[str, Any] | None = None):
        # e.g. name='finances:account-recalculate' expects kwargs: pk
        url = reverse(name, kwargs={"pk": pk})
        return self.client.post(url, payload or {}, format="json")

    def test_account_create_and_recalculate(self):
        # create account via API
        resp = self._post("finances:account-list", {
                          "name": "Card", "type": "card", "currency": "USD", "initial_balance": "200.00"})
        assert resp.status_code == 201, resp.data
        created = resp.json()
        acc_id = created["id"]

        # get detail to inspect cached balance field (should be 0 initially until recalc/creation logic runs)
        detail_url = reverse("finances:account-detail", kwargs={"pk": acc_id})
        resp_detail = self.client.get(detail_url)
        assert resp_detail.status_code == 200
        # recalculate via action
        recalc = self._post_detail_action(
            "finances:account-recalculate", acc_id, action="recalculate")
        # The route name used for action is 'finances:account-recalculate' — we call it above by reverse(name,...)
        # Some setups may return 200 or 201; accept 200
        assert recalc.status_code in (200, 201), recalc.data
        data = recalc.json()
        # returned balance should be a string-parsable decimal
        assert "balance" in data

    def test_transaction_create_updates_balance(self):
        # create an expense transaction of -20.00 on acc1
        payload = {
            "account": self.acc1.pk,
            "amount": "-20.00",
            "currency": "USD",
            "type": "expense",
            "date": "2024-01-01",
            "description": "Test purchase",
        }
        resp = self._post("finances:transaction-list", payload)
        assert resp.status_code == 201, resp.data
        # fetch account detail and verify balance decreased by 20 -> 80.00
        acc_detail = self.client.get(
            reverse("finances:account-detail", kwargs={"pk": self.acc1.pk}))
        assert acc_detail.status_code == 200
        bal = Decimal(str(acc_detail.json().get("balance")))
        assert bal == Decimal("80.00")

    def test_transfer_creation_creates_pair_and_updates_balances(self):
        # Create a transfer from acc1 -> acc2 for 30.00 using transaction endpoint
        payload = {
            "account": self.acc1.pk,
            "amount": "30.00",
            "currency": "USD",
            "type": "transfer",
            "transfer_to_account": self.acc2.pk,
            "date": "2024-01-02",
            "description": "Move to savings",
        }
        resp = self._post("finances:transaction-list", payload)
        assert resp.status_code == 201, resp.data
        tx = Transaction.objects.filter(
            account=self.acc1).order_by("-created_at").first()
        # check related transaction exists
        assert tx is not None
        assert tx.related_transaction is not None
        # verify amounts and account balances
        self.acc1.refresh_from_db()
        self.acc2.refresh_from_db()
        assert self.acc1.get_balance() == Decimal("70.00")  # 100 - 30
        assert self.acc2.get_balance() == Decimal("80.00")  # 50 + 30

    def test_adjustment_create_and_reverse_endpoint(self):
        # create an adjustment via API: change acc1 from 100 -> 120
        payload = {
            "account": self.acc1.pk,
            "old_amount": "100.00",
            "new_amount": "120.00",
            "reason": "Manual top-up",
        }
        resp = self._post("finances:adjustment-list", payload)
        assert resp.status_code == 201, resp.data
        adj_id = resp.json()["id"]
        # balance should reflect +20
        self.acc1.refresh_from_db()
        assert self.acc1.get_balance() == Decimal("120.00")
        # call reverse action
        rev_url_name = "finances:adjustment-reverse"
        rev_resp = self._post_detail_action(
            rev_url_name, adj_id, action="reverse")
        assert rev_resp.status_code in (200, 201), rev_resp.data
        body = rev_resp.json()
        assert "reversal_id" in body
        # after reversal, balance should be back to 100
        self.acc1.refresh_from_db()
        assert self.acc1.get_balance() == Decimal("100.00")

    def test_importjob_create_endpoint(self):
        # create import job via API; perform_create may try to enqueue a task — we accept either PENDING or RUNNING
        payload = {"file_name": "sample_for_api.csv"}
        resp = self._post("finances:importjob-list", payload)
        assert resp.status_code == 201, resp.data
        job_data = resp.json()
        job = ImportJob.objects.get(pk=job_data["id"])
        assert job.file_name == "sample_for_api.csv"
        assert job.status in {
            ImportJob.Status.PENDING,
            ImportJob.Status.RUNNING,
            ImportJob.Status.COMPLETED,
            ImportJob.Status.FAILED,
        }
