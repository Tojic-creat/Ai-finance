# ml_service/app/main.py
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from threading import Lock

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

# ML imports
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import numpy as np

# Configure simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("finassist-ml")

APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODEL_DIR / "text_classifier.joblib"
META_PATH = MODEL_DIR / "meta.json"

app = FastAPI(title="finassist-ml", version="0.1.0")

# concurrency lock to prevent concurrent train/predict race conditions
_model_lock = Lock()

# ---------- Pydantic schemas ----------


class HealthResponse(BaseModel):
    status: str = "ok"


class PredictRequest(BaseModel):
    text: str = Field(..., example="Payment to ACME store for groceries")


class CategoryScore(BaseModel):
    category: str
    confidence: float


class PredictResponse(BaseModel):
    top3: List[CategoryScore]


class TrainItem(BaseModel):
    text: str
    label: str


class TrainRequest(BaseModel):
    items: List[TrainItem] = Field(..., min_items=1)


class TrainResponse(BaseModel):
    n_samples: int
    labels: List[str]
    accuracy: Optional[float] = None
    message: Optional[str] = None


# ---------- Helpers: model persistence & utilities ----------
def _model_exists() -> bool:
    return MODEL_PATH.exists() and META_PATH.exists()


def _save_model(pipe: Pipeline, labels: List[str]) -> None:
    joblib.dump(pipe, MODEL_PATH)
    with META_PATH.open("w", encoding="utf-8") as f:
        json.dump({"labels": labels}, f)
    logger.info("Model and metadata saved to %s", MODEL_DIR)


def _load_model() -> Optional[Dict[str, Any]]:
    """
    Load model pipeline and metadata. Returns dict with 'pipeline' and 'labels' keys or None.
    """
    if not _model_exists():
        return None
    try:
        pipe = joblib.load(MODEL_PATH)
        with META_PATH.open("r", encoding="utf-8") as f:
            meta = json.load(f)
        labels = meta.get("labels", [])
        return {"pipeline": pipe, "labels": labels}
    except Exception as e:
        logger.exception("Failed to load model: %s", e)
        return None


def _ensure_trained_or_raise():
    if not _model_exists():
        raise HTTPException(
            status_code=503, detail="Model not trained yet. Call /train first.")


def _top_n_from_proba(proba: np.ndarray, labels: List[str], n: int = 3) -> List[Dict[str, Any]]:
    """
    proba: 1D array of probabilities per label (shape = (n_labels,))
    returns list of dicts sorted by prob desc
    """
    order = np.argsort(proba)[::-1]
    top = []
    for idx in order[:n]:
        top.append({"category": labels[int(idx)], "confidence": float(
            round(proba[int(idx)], 4))})
    return top


# ---------- Endpoints ----------
@app.get("/health", response_model=HealthResponse)
def health():
    """Simple health check"""
    return HealthResponse(status="ok")


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    """
    Predict top-3 categories for free text.
    If model is not trained, returns a graceful 'unknown' response.
    """
    logger.info("Predict request received")
    model_bundle = _load_model()
    if model_bundle is None:
        # graceful fallback — model not trained
        logger.warning("Predict called but no model available")
        return PredictResponse(top3=[CategoryScore(category="unknown", confidence=0.0)])

    pipe: Pipeline = model_bundle["pipeline"]
    labels: List[str] = model_bundle["labels"]

    text = req.text or ""
    if not text.strip():
        raise HTTPException(
            status_code=400, detail="Empty text provided for prediction.")

    try:
        with _model_lock:
            # pipeline should expose predict_proba
            probs = pipe.predict_proba([text])[0]  # shape (n_labels,)
    except Exception as e:
        logger.exception("Prediction error: %s", e)
        raise HTTPException(
            status_code=500, detail="Prediction failed due to internal error.")

    top3 = _top_n_from_proba(probs, labels, n=3)
    return PredictResponse(top3=[CategoryScore(**t) for t in top3])


@app.post("/train", response_model=TrainResponse)
def train(req: TrainRequest):
    """
    Train a simple text classification pipeline:
      - TF-IDF vectorizer
      - LogisticRegression (multiclass)
    Endpoint returns accuracy on a holdout (20%) and persists model to disk.
    """
    logger.info("Train request received with %d items", len(req.items))
    texts = [it.text for it in req.items]
    labels = [it.label for it in req.items]

    if len(texts) < 5:
        # nudging a minimum amount of data
        message = "Provide at least 5 labeled examples for a minimally useful model."
        logger.warning(message)
        # proceed training anyway but warn in response
        warn_msg = message
    else:
        warn_msg = None

    # train/test split
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            texts, labels, test_size=0.2, random_state=42, stratify=labels if len(set(labels)) > 1 else None
        )
    except ValueError:
        # fallback if stratify fails (e.g., single class)
        X_train, X_test, y_train, y_test = train_test_split(
            texts, labels, test_size=0.2, random_state=42)

    # Build pipeline
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=25_000)),
            ("clf", LogisticRegression(max_iter=200,
             multi_class="auto", solver="lbfgs")),
        ]
    )

    try:
        with _model_lock:
            pipeline.fit(X_train, y_train)
            # Evaluate
            y_pred = pipeline.predict(X_test) if len(
                X_test) > 0 else pipeline.predict(X_train)
            acc = float(round(accuracy_score(
                y_test if len(X_test) > 0 else y_train, y_pred), 4))
            # compute label order used by predict_proba
            # sklearn's classes_ attribute corresponds to clf.classes_
            if hasattr(pipeline.named_steps["clf"], "classes_"):
                label_order = [str(x)
                               for x in pipeline.named_steps["clf"].classes_]
            else:
                label_order = sorted(list(set(labels)))
            # persist pipeline + metadata
            _save_model(pipeline, label_order)
    except Exception as e:
        logger.exception("Training failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Training failed: {e}")

    response = TrainResponse(n_samples=len(
        texts), labels=label_order, accuracy=acc, message=warn_msg)
    logger.info("Training completed: samples=%d accuracy=%s", len(texts), acc)
    return response


# Optional convenience: endpoint to show model metadata (not required but handy)
@app.get("/model/meta")
def model_meta():
    """Return simple metadata about the trained model (if any)."""
    bundle = _load_model()
    if bundle is None:
        return JSONResponse(status_code=404, content={"detail": "No model trained yet."})
    return {"labels": bundle["labels"]}
