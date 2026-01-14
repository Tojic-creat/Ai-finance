# ml_service/app/schemas.py
"""
Pydantic schemas for ML service endpoints.

Includes:
 - PredictRequest / PredictBatchRequest
 - CategoryScore (single label + confidence)
 - PredictResponse
 - TrainRequest / TrainResponse
 - FeedbackRequest / FeedbackResponse

These schemas are intentionally lightweight and include basic validation
(e.g. confidence in [0,1], top-k length limits).
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, conlist, confloat, constr


class CategoryScore(BaseModel):
    """
    Single category with confidence score (0.0 .. 1.0).
    """
    category: constr(strip_whitespace=True, min_length=1)
    confidence: confloat(ge=0.0, le=1.0)

    class Config:
        schema_extra = {
            "example": {"category": "groceries", "confidence": 0.92}
        }


class PredictRequest(BaseModel):
    """
    Request for single-text prediction.
    """
    text: constr(strip_whitespace=True, min_length=1) = Field(
        ...,
        description="Transaction description or any text to classify"
    )

    class Config:
        schema_extra = {
            "example": {"text": "Starbucks purchase $4.50 - latte"}
        }


class PredictBatchRequest(BaseModel):
    """
    Request for batch prediction (multiple texts).
    """
    texts: conlist(constr(strip_whitespace=True, min_length=1), min_items=1) = Field(
        ...,
        description="List of texts to classify"
    )
    top_k: int = Field(
        3, ge=1, le=20, description="Number of top categories to return per text")

    class Config:
        schema_extra = {
            "example": {
                "texts": [
                    "Uber trip to downtown $12.30",
                    "Salary payment from ACME corp"
                ],
                "top_k": 3
            }
        }


class PredictResponse(BaseModel):
    """
    Response for a single prediction: top-N category scores.
    """
    top3: conlist(CategoryScore, min_items=1) = Field(...,
                                                      description="Top predicted categories (sorted desc by confidence)")

    class Config:
        schema_extra = {
            "example": {
                "top3": [
                    {"category": "transport", "confidence": 0.87},
                    {"category": "food", "confidence": 0.07},
                    {"category": "others", "confidence": 0.03}
                ]
            }
        }


class BatchItemPrediction(BaseModel):
    text: str
    predictions: conlist(CategoryScore, min_items=1)

    class Config:
        schema_extra = {
            "example": {
                "text": "Coffee shop",
                "predictions": [
                    {"category": "food", "confidence": 0.9},
                    {"category": "entertainment", "confidence": 0.05}
                ]
            }
        }


class PredictBatchResponse(BaseModel):
    results: conlist(BatchItemPrediction, min_items=1)


# ---------------------------
# Training / management APIs
# ---------------------------
class TrainRequest(BaseModel):
    """
    Request payload to trigger a train job.
    For MVP we'll accept either a path to a dataset (CSV/NDJSON) or rely on
    default internal dataset.
    """
    dataset_path: Optional[str] = Field(
        None, description="Optional path (or URL) to training dataset")
    epochs: int = Field(
        1, ge=1, le=100, description="Number of training epochs (if applicable)")
    validate_split: float = Field(
        0.1, ge=0.0, le=0.5, description="Fraction of data to reserve for validation")

    class Config:
        schema_extra = {
            "example": {"dataset_path": None, "epochs": 3, "validate_split": 0.1}
        }


class TrainMetrics(BaseModel):
    train_loss: Optional[float]
    val_loss: Optional[float]
    accuracy: Optional[float]
    f1_micro: Optional[float]
    f1_macro: Optional[float]


class TrainResponse(BaseModel):
    status: str = Field(...,
                        description="Job status: queued/running/done/failed")
    message: Optional[str] = None
    metrics: Optional[TrainMetrics] = None


# ---------------------------
# Feedback API
# ---------------------------
class FeedbackRequest(BaseModel):
    """
    User feedback about a prediction.

    - text: original transaction text
    - selected_category: category chosen/confirmed by user
    - predicted_top: optional list of predicted categories returned earlier
    - user_id: optional, anonymized id if available
    - context_id: optional id to tie feedback to a stored prediction/transaction
    """
    text: constr(strip_whitespace=True, min_length=1)
    selected_category: constr(strip_whitespace=True, min_length=1)
    predicted_top: Optional[List[CategoryScore]] = None
    user_id: Optional[str] = None
    context_id: Optional[str] = None
    helpful: Optional[bool] = Field(
        None, description="Did the user find the suggestion helpful? (optional)")

    class Config:
        schema_extra = {
            "example": {
                "text": "Pizza Hut order 15.20",
                "selected_category": "food",
                "predicted_top": [
                    {"category": "food", "confidence": 0.7},
                    {"category": "entertainment", "confidence": 0.1}
                ],
                "user_id": "anon-123",
                "context_id": "txn-20250101-42",
                "helpful": True
            }
        }


class FeedbackResponse(BaseModel):
    status: str = Field(..., description="ok / error")
    message: Optional[str] = None
