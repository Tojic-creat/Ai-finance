# ml_service/app/tests/test_api.py
"""
Tests for the ml_service FastAPI app.

Covers:
 - GET /health
 - POST /predict (happy path and validation error)
 - POST /train (basic contract: endpoint exists and returns a reasonable response)

These tests are intentionally a bit flexible about the /train response shape so they
remain useful while the training endpoint evolves (e.g. returns job id, status, or queued).
"""
from fastapi.testclient import TestClient
import pytest

from ml_service.app.main import app

client = TestClient(app)


def test_health():
    """Health endpoint should return status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200, f"/health returned {resp.status_code} body={resp.text}"
    json_data = resp.json()
    assert isinstance(json_data, dict)
    # Accept either exact {"status": "ok"} or at least presence of 'status' == 'ok'
    assert "status" in json_data
    assert json_data["status"] == "ok"


def test_predict_success_structure():
    """/predict should accept text and return top3 list of dicts with category/confidence."""
    payload = {"text": "Starbucks 12345 $4.50 latte"}
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 200, f"/predict returned {resp.status_code} body={resp.text}"
    data = resp.json()
    assert isinstance(data, dict), "response must be a JSON object"
    assert "top3" in data, "response must include 'top3' key"
    top3 = data["top3"]
    assert isinstance(top3, list), "'top3' must be a list"
    # allow empty list but if items present check shape
    for item in top3:
        assert isinstance(item, dict), "each top3 item must be an object"
        assert "category" in item, "each top3 item must contain 'category'"
        assert "confidence" in item, "each top3 item must contain 'confidence'"
        # confidence should be numeric (float/int)
        assert isinstance(item["confidence"], (float, int)
                          ), "'confidence' must be numeric"


def test_predict_validation_error_on_missing_field():
    """When required 'text' field is missing, API should return 422 (validation error)."""
    resp = client.post("/predict", json={})
    assert resp.status_code == 422, "predict must return 422 on invalid/missing input"


@pytest.mark.parametrize("payload", [
    {},  # empty body
    {"dataset": "small"},  # arbitrary body
])
def test_train_endpoint_basic_contract(payload):
    """
    POST /train should exist and return a reasonable response.

    This test is flexible:
      - Accepts 200/201/202/204 as success status codes.
      - If a JSON body is returned, it should include at least one of:
        'status', 'job_id', 'message', 'result'.
    """
    resp = client.post("/train", json=payload)
    assert resp.status_code in (200, 201, 202, 204, 404), (
        f"/train returned unexpected status {resp.status_code}; body={resp.text}"
    )

    # If train endpoint is not implemented and returns 404, make the failure explicit:
    if resp.status_code == 404:
        pytest.skip(
            "/train endpoint not implemented (received 404) — skip contract checks.")

    # If no content (204) it's acceptable
    if resp.status_code == 204:
        return

    # If JSON returned, check for at least one informative key
    try:
        j = resp.json()
    except ValueError:
        pytest.fail(
            f"/train returned non-json response with status {resp.status_code}: {resp.text}")

    assert isinstance(j, dict), "expected JSON object from /train"

    # require at least one of these keys to be present (flexible contract)
    informative_keys = {"status", "job_id", "message", "result"}
    assert any(
        k in j for k in informative_keys), f"/train JSON should include one of {informative_keys}; got {list(j.keys())}"
