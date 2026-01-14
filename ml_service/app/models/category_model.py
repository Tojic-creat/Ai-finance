# ml_service/app/models/category_model.py
"""
Wrapper для загрузки/сохранения sklearn Pipeline и удобного inference (top-k).
Ожидает, что модель сохранена joblib'ом и мета-информация (labels order) — в JSON файле.
Совместим с pipeline'ом из ml_service/app/main.py (TfidfVectorizer + LogisticRegression).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np

logger = logging.getLogger("finassist-ml.category_model")
logger.addHandler(logging.NullHandler())

# Default locations (relative to this file)
BASE_DIR = Path(__file__).resolve().parent.parent  # ml_service/app
DEFAULT_MODEL_DIR = BASE_DIR.parent / "models"
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "text_classifier.joblib"
DEFAULT_META_PATH = DEFAULT_MODEL_DIR / "meta.json"


class CategoryModel:
    """
    Lightweight wrapper around a scikit-learn text classification pipeline.
    Responsibilities:
      - load/save pipeline + label order metadata
      - provide predict_topk(text, k) -> [(label, confidence), ...]
      - be thread-safe for load/save operations
    """

    def __init__(
        self,
        model_path: Path = DEFAULT_MODEL_PATH,
        meta_path: Path = DEFAULT_META_PATH,
    ) -> None:
        self.model_path = Path(model_path)
        self.meta_path = Path(meta_path)
        self._pipeline = None  # type: Optional[Any]
        self._labels = []  # type: List[str]
        self._lock = Lock()

    # -------------------------
    # Persistence / lifecycle
    # -------------------------
    def exists(self) -> bool:
        """Return True if both model and metadata files exist."""
        return self.model_path.exists() and self.meta_path.exists()

    def load(self) -> bool:
        """
        Load pipeline and metadata from disk.
        Returns True on success, False on failure.
        """
        with self._lock:
            if not self.exists():
                logger.debug("Model or meta file not found: %s, %s",
                             self.model_path, self.meta_path)
                return False
            try:
                pipeline = joblib.load(self.model_path)
                with self.meta_path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
                labels = meta.get("labels", [])
                if not isinstance(labels, list):
                    raise ValueError("meta.json 'labels' field must be a list")
                # assign only after successful load
                self._pipeline = pipeline
                self._labels = [str(x) for x in labels]
                logger.info("Model loaded from %s (labels=%d)",
                            self.model_path, len(self._labels))
                return True
            except Exception as e:
                logger.exception("Failed to load model/meta: %s", e)
                # keep previous state if exists
                return False

    def save(self, pipeline: Any, labels: Sequence[str]) -> None:
        """
        Persist pipeline and labels metadata to disk atomically (best-effort).
        Overwrites existing files.
        """
        DEFAULT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp_model = self.model_path.with_suffix(".joblib.tmp")
        tmp_meta = self.meta_path.with_suffix(".json.tmp")

        with self._lock:
            try:
                joblib.dump(pipeline, tmp_model)
                with tmp_meta.open("w", encoding="utf-8") as f:
                    json.dump({"labels": list(labels)}, f, ensure_ascii=False)
                # atomic-ish replace
                tmp_model.replace(self.model_path)
                tmp_meta.replace(self.meta_path)
                # update in-memory pointers
                self._pipeline = pipeline
                self._labels = [str(x) for x in labels]
                logger.info("Model and meta saved to %s", self.model_path)
            except Exception as e:
                logger.exception("Failed to save model/meta: %s", e)
                # cleanup tmp files
                try:
                    if tmp_model.exists():
                        tmp_model.unlink()
                except Exception:
                    pass
                try:
                    if tmp_meta.exists():
                        tmp_meta.unlink()
                except Exception:
                    pass
                raise

    # -------------------------
    # Accessors
    # -------------------------
    def is_ready(self) -> bool:
        """True if model loaded in memory or persisted on disk."""
        if self._pipeline is not None and self._labels:
            return True
        return self.exists()

    def get_labels(self) -> List[str]:
        """Return label list (loads from disk if necessary)."""
        if not self._labels and self.exists():
            self.load()
        return list(self._labels)

    # -------------------------
    # Inference utilities
    # -------------------------
    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        """Numerically-stable softmax for 1D array."""
        x = np.asarray(x, dtype=float)
        x = x - np.max(x)
        exp = np.exp(x)
        return exp / np.sum(exp)

    def _ensure_pipeline_loaded(self) -> None:
        """Load pipeline into memory if not already loaded. Raises RuntimeError if unavailable."""
        if self._pipeline is None:
            loaded = self.load()
            if not loaded:
                raise RuntimeError(
                    "Model not available. Call load() or train/save a model first.")

    def predict_proba(self, texts: Sequence[str]) -> List[List[float]]:
        """
        Predict probability distribution for each input text.
        Returns list of probability vectors (ordered according to self._labels).
        """
        self._ensure_pipeline_loaded()
        pipe = self._pipeline
        # try predict_proba
        try:
            proba = pipe.predict_proba(list(texts))
            # sklearn returns shape (n_samples, n_labels)
            return [list(map(float, row)) for row in proba]
        except Exception:
            # try decision_function -> softmax
            try:
                scores = pipe.decision_function(list(texts))
                # decision_function can return (n_samples, n_labels) or (n_samples,) for binary
                arr = np.atleast_2d(scores)
                probs = np.apply_along_axis(self._softmax, 1, arr)
                return [list(map(float, row)) for row in probs]
            except Exception as e:
                logger.exception(
                    "Model does not support predict_proba or decision_function: %s", e)
                raise RuntimeError("Model cannot produce probability scores.")

    def predict_topk(self, text: str, k: int = 3) -> List[Tuple[str, float]]:
        """
        Predict top-k labels for a single text.
        Returns list of (label, confidence) sorted by confidence desc.
        """
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if k <= 0:
            raise ValueError("k must be positive")

        self._ensure_pipeline_loaded()
        labels = self.get_labels()
        if not labels:
            raise RuntimeError("No labels available in model metadata.")

        probs = self.predict_proba([text])[0]  # first (and only) sample
        if len(probs) != len(labels):
            # If classifier uses different label order, attempt to fetch classes_ if available
            try:
                clf = getattr(self._pipeline.named_steps.get(
                    "clf", self._pipeline), "classes_", None)
                if clf is not None:
                    labels = [str(x) for x in clf]
                # else keep existing labels
            except Exception:
                pass

        order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
        top = []
        for idx in order[:k]:
            lbl = labels[idx] if idx < len(labels) else f"label_{idx}"
            top.append((lbl, float(round(probs[idx], 6))))
        return top

    # Convenience singleton-like instance for simple imports
    # (e.g., from ml_service.app.models.category_model import default_model)
    # but creation is lightweight so user can instantiate as needed.


# module-level default instance
default_model = CategoryModel()
