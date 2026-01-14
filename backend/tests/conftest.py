# backend/tests/conftest.py
import io
import csv
import os
import tempfile
import shutil
import importlib
from typing import Any, Dict

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

try:
    # DRF client might not be installed in every environment but most projects use it
    from rest_framework.test import APIClient
except Exception:
    APIClient = None  # type: ignore


@pytest.fixture
def user_model():
    return get_user_model()


@pytest.fixture
def create_user(user_model):
    """
    Returns a helper function to create users quickly:
        user = create_user(username='alice', email='a@b', password='pwd', is_staff=True)
    """
    def _create_user(**kwargs):
        password = kwargs.pop('password', 'password123')
        is_super = kwargs.pop('is_superuser', kwargs.pop('is_super', False))
        is_staff = kwargs.pop('is_staff', False)
        username = kwargs.get('username') or kwargs.get(
            'email') or f'user_{timezone.now().timestamp()}'
        kwargs.setdefault('username', username)
        if is_super:
            user = user_model.objects.create_superuser(
                **{**kwargs, 'password': password})
        else:
            user = user_model.objects.create_user(
                **{**kwargs, 'password': password})
            if is_staff:
                user.is_staff = True
                user.save()
        return user
    return _create_user


@pytest.fixture
def regular_user(create_user):
    return create_user(username='testuser', email='testuser@example.com', password='pass')


@pytest.fixture
def admin_user(create_user):
    return create_user(username='admin', email='admin@example.com', password='adminpass', is_super=True)


@pytest.fixture
def client():
    """
    Basic Django test client instance (pytest-django also provides `client` fixture;
    this one ensures it's available regardless).
    """
    from django.test import Client as DjangoClient
    return DjangoClient()


@pytest.fixture
def auth_client(client, regular_user):
    """
    Django client authenticated as `regular_user`.
    """
    client.force_login(regular_user)
    return client


@pytest.fixture
def api_client(regular_user):
    """
    DRF APIClient with force-authentication (if DRF is present).
    """
    if APIClient is None:
        pytest.skip(
            "djangorestframework not installed - skipping api_client fixture")
    c = APIClient()
    c.force_authenticate(user=regular_user)
    return c


@pytest.fixture(scope="function")
def temp_media_root(tmp_path, monkeypatch):
    """
    Provide a temporary MEDIA_ROOT and clean up after test.
    Useful for file upload tests.
    """
    tmp_media = tmp_path / "media"
    tmp_media.mkdir()
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_media), raising=False)
    yield tmp_media
    # cleanup happens automatically by tmp_path fixture


def _import_finances_models():
    """
    Try to import the finances models module and return it or raise ImportError.
    """
    mod_path = "backend.apps.finances.models"
    try:
        mod = importlib.import_module(mod_path)
    except Exception as exc:
        raise ImportError(f"Could not import {mod_path}: {exc}") from exc
    return mod


def _pick_kwargs_for_model(model: models.Model, desired: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a model class and a dict of desired logical attrs (like 'user', 'name', 'balance'),
    try to map into actual field names present on model._meta.fields.
    Returns kwargs that can be passed to model.objects.create(**kwargs)
    """
    field_names = {f.name for f in model._meta.get_fields()
                   if hasattr(f, "name")}
    kwargs = {}
    for logical_name, value in desired.items():
        # common variants mapping
        candidates = []
        if logical_name == "user":
            candidates = ["user", "owner", "created_by", "account_user"]
        elif logical_name == "name":
            candidates = ["name", "title", "label"]
        elif logical_name in ("balance", "initial_balance", "amount"):
            candidates = ["balance", "initial_balance",
                          "amount", "starting_balance"]
        elif logical_name == "currency":
            candidates = ["currency", "currency_code"]
        elif logical_name == "type":
            candidates = ["type", "account_type", "kind"]
        elif logical_name == "amount":
            candidates = ["amount", "value", "sum"]
        elif logical_name == "category":
            candidates = ["category", "cat", "category_id"]
        elif logical_name == "reason":
            candidates = ["reason", "note", "description"]
        elif logical_name == "transaction_type":
            candidates = ["type", "transaction_type", "kind"]
        else:
            candidates = [logical_name]

        # choose first candidate that is actual field
        for c in candidates:
            if c in field_names:
                kwargs[c] = value
                break
        # If none matched, try to set any field that looks similar by substring
        if logical_name not in kwargs:
            for f in field_names:
                if logical_name in f:
                    kwargs[f] = value
                    break
    return kwargs


@pytest.fixture
def finances_models():
    """
    Attempt to import finances.models and return the module.
    Tests using it should mark db usage (pytest.mark.django_db) if they touch DB.
    """
    return _import_finances_models()


@pytest.fixture
def create_account(finances_models, regular_user):
    """
    Helper to create a minimal account instance for tests.
    Attempts to auto-map common fields: user/owner, name, balance, currency, type.
    Returns the created model instance.
    """
    Account = getattr(finances_models, "Account", None)
    if Account is None:
        raise ImportError(
            "Account model not found in backend.apps.finances.models")

    def _create(**overrides):
        desired = {
            "user": regular_user,
            "name": overrides.pop("name", "Test Account"),
            "balance": overrides.pop("balance", 1000),
            "currency": overrides.pop("currency", "USD"),
            "type": overrides.pop("type", "card"),
        }
        desired.update(overrides)
        kwargs = _pick_kwargs_for_model(Account, desired)
        return Account.objects.create(**kwargs)
    return _create


@pytest.fixture
def create_transaction(finances_models, regular_user):
    """
    Helper to create a transaction. Maps common fields.
    """
    Transaction = getattr(finances_models, "Transaction", None)
    if Transaction is None:
        # fallback to Transaction class name variations
        Transaction = getattr(finances_models, "Operation", None)

    if Transaction is None:
        raise ImportError(
            "Transaction/Operation model not found in backend.apps.finances.models")

    def _create(account=None, **overrides):
        desired = {
            "user": regular_user,
            "amount": overrides.pop("amount", 10.0),
            "category": overrides.pop("category", None),
            "transaction_type": overrides.pop("transaction_type", "expense"),
            "reason": overrides.pop("reason", ""),
            "date": overrides.pop("date", timezone.now()),
        }
        # include account if provided and model has a matching field
        if account is not None:
            desired["account"] = account
        desired.update(overrides)
        kwargs = _pick_kwargs_for_model(Transaction, desired)
        return Transaction.objects.create(**kwargs)
    return _create


@pytest.fixture
def create_adjustment(finances_models, regular_user):
    """
    Helper to create an adjustment (manual balance change). Maps common fields.
    """
    Adjustment = getattr(finances_models, "Adjustment", None)
    if Adjustment is None:
        # maybe named BalanceAdjustment or AccountAdjustment
        Adjustment = getattr(finances_models, "AccountAdjustment", None)

    if Adjustment is None:
        raise ImportError(
            "Adjustment model not found in backend.apps.finances.models")

    def _create(account=None, old_amount=None, new_amount=None, reason="test adjustment", **overrides):
        desired = {
            "user": regular_user,
            "reason": reason,
            "date": overrides.pop("date", timezone.now()),
        }
        if account is not None:
            desired["account"] = account
        if old_amount is not None:
            desired["old_amount"] = old_amount
        if new_amount is not None:
            desired["new_amount"] = new_amount
        desired.update(overrides)
        kwargs = _pick_kwargs_for_model(Adjustment, desired)
        return Adjustment.objects.create(**kwargs)
    return _create


@pytest.fixture
def sample_csv(tmp_path):
    """
    Create a sample CSV file following a simple template useful for import tests.
    Returns the path to the CSV file.
    """
    headers = ["date", "amount", "currency", "description", "account"]
    rows = [
        ["2025-01-01", "100.00", "USD", "Salary", "Checking"],
        ["2025-01-02", "-12.50", "USD", "Coffee", "Checking"],
        ["2025-01-03", "-50.00", "USD", "Groceries", "Credit Card"],
    ]
    p = tmp_path / "sample_import.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return str(p)


@pytest.fixture
def patch_celery_eager(monkeypatch):
    """
    Ensure tasks run eagerly (synchronously) in tests if Celery is used.
    This sets common Celery settings used by projects; it's tolerant if these names don't exist.
    """
    # common names used across projects
    monkeypatch.setattr(
        settings, "CELERY_TASK_ALWAYS_EAGER", True, raising=False)
    monkeypatch.setattr(
        settings, "CELERY_TASK_EAGER_PROPAGATES", True, raising=False)
    # older setting name
    monkeypatch.setattr(settings, "TASK_ALWAYS_EAGER", True, raising=False)
    yield
    # settings are restored by monkeypatch automatically


@pytest.fixture
def mock_ai_service(monkeypatch):
    """
    Patch-out external AI/ML call used by finances service layer (if exists).
    This returns a helper to set the mocked response.
    Usage in tests:
        mock_ai = mock_ai_service()
        mock_ai.set_response({"categories": [{"label":"food","score":0.9}]})
    """
    # candidate paths where the project might call out to AI service
    candidates = [
        "backend.apps.finances.services.call_ai",
        "backend.apps.finances.services.classify_transaction",
        "backend.apps.finances.utils.call_ai",
        "backend.apps.finances.utils.classify_transaction",
    ]

    class MockAI:
        def __init__(self):
            self._response = {"categories": []}

        def set_response(self, resp):
            self._response = resp

        def _stub(*args, **kwargs):
            # always return current response
            return mock._response

    mock = MockAI()
    # Attach to first candidate that exists; if none exist, provide dummy catcher
    attached = False
    for path in candidates:
        module_path, _, attr = path.rpartition(".")
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, lambda *a, **k: mock._response)
                attached = True
                break
        except Exception:
            continue

    # If nothing attached, expose simple callable under this fixture only (tests can call it directly)
    def _get():
        return mock

    return _get


# End of conftest.py
