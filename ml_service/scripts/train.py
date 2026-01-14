# ml_service/scripts/train.py
"""
Simple local training script for the finassist-ml service.

Expected CSV format:
    text,category
    "Starbucks receipt latte","food_and_drink"
    "Salary from ACME corp","income"

This script builds a simple sklearn pipeline (CountVectorizer -> TfidfTransformer -> LogisticRegression),
trains it, prints evaluation metrics, and saves the trained pipeline to disk (joblib).

Usage:
    python ml_service/scripts/train.py --data-path ml_service/data/train.csv \
        --output-path ml_service/models/pipeline.joblib --test-size 0.2

If the output directory doesn't exist it will be created.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Dict, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer, TfidfTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ml_train")


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    df = pd.read_csv(path)
    if "text" not in df.columns or "category" not in df.columns:
        raise ValueError("CSV must contain 'text' and 'category' columns")
    df = df.dropna(subset=["text", "category"])
    return df


def build_pipeline(max_features: int = 20000, C: float = 1.0) -> Pipeline:
    """
    Build sklearn Pipeline:
      CountVectorizer -> TfidfTransformer -> LogisticRegression
    """
    vect = CountVectorizer(max_features=max_features,
                           ngram_range=(1, 2), analyzer="word")
    tfidf = TfidfTransformer()
    clf = LogisticRegression(C=C, max_iter=200, n_jobs=-1)
    pipeline = Pipeline([("vect", vect), ("tfidf", tfidf), ("clf", clf)])
    return pipeline


def train_pipeline(
    pipeline: Pipeline,
    texts: pd.Series,
    labels: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
) -> Tuple[Pipeline, Dict]:
    """
    Train pipeline and return (fitted_pipeline, metrics_dict)
    """
    X_train, X_test, y_train, y_test = train_test_split(
        texts.values, labels.values, test_size=test_size, random_state=random_state, stratify=labels.values
    )

    logger.info("Training on %d samples, validating on %d samples",
                len(X_train), len(X_test))
    pipeline.fit(X_train, y_train)

    preds = pipeline.predict(X_test)
    report = classification_report(
        y_test, preds, output_dict=True, zero_division=0)
    macro_f1 = f1_score(y_test, preds, average="macro", zero_division=0)
    metrics = {"classification_report": report, "macro_f1": float(
        macro_f1), "n_train": len(X_train), "n_test": len(X_test)}
    return pipeline, metrics


def save_pipeline(pipeline: Pipeline, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, output_path)
    logger.info("Saved trained pipeline to %s", output_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train a simple text classification pipeline and save it.")
    p.add_argument("--data-path", type=Path, required=True,
                   help="Path to CSV file with columns 'text' and 'category'")
    p.add_argument(
        "--output-path",
        type=Path,
        default=Path("ml_service/models/pipeline.joblib"),
        help="Path where the trained pipeline will be saved (joblib)",
    )
    p.add_argument("--test-size", type=float, default=0.2,
                   help="Validation split fraction")
    p.add_argument("--max-features", type=int, default=20000,
                   help="Max features for CountVectorizer")
    p.add_argument("--C", type=float, default=1.0,
                   help="Regularization strength for LogisticRegression (inverse)")
    p.add_argument("--random-state", type=int, default=42, help="Random seed")
    p.add_argument("--metrics-out", type=Path, default=None,
                   help="Optional path to write metrics JSON")
    return p.parse_args()


def main():
    args = parse_args()
    logger.info("Loading data from %s", args.data_path)
    df = load_csv(args.data_path)

    logger.info("Found %d rows in dataset", len(df))
    # simple preprocessing: ensure strings
    df["text"] = df["text"].astype(str)
    df["category"] = df["category"].astype(str)

    pipeline = build_pipeline(max_features=args.max_features, C=args.C)

    trained_pipeline, metrics = train_pipeline(
        pipeline, df["text"], df["category"], test_size=args.test_size, random_state=args.random_state
    )

    save_pipeline(trained_pipeline, args.output_path)

    # print metrics summary
    logger.info("Training finished. macro_f1=%.4f", metrics["macro_f1"])
    logger.info("Classification report (top-level keys): %s",
                ", ".join(metrics["classification_report"].keys()))

    if args.metrics_out:
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.metrics_out, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, ensure_ascii=False, indent=2)
        logger.info("Saved metrics JSON to %s", args.metrics_out)

    # Also print a human-friendly report
    print("\n=== Training summary ===")
    print(
        f"Train samples: {metrics['n_train']}, Validation samples: {metrics['n_test']}")
    print(f"Macro F1: {metrics['macro_f1']:.4f}")
    print("\nPer-class F1:")
    # classification_report dict includes labels and 'macro avg', 'weighted avg', etc.
    report = metrics["classification_report"]
    for label, vals in report.items():
        if label in ("accuracy", "macro avg", "weighted avg", "micro avg"):
            continue
        precision = vals.get("precision", 0.0)
        recall = vals.get("recall", 0.0)
        f1 = vals.get("f1-score", 0.0)
        support = vals.get("support", 0)
        print(
            f"  {label:20s} precision={precision:.3f} recall={recall:.3f} f1={f1:.3f} support={support}")

    print("\nModel saved to:", args.output_path)


if __name__ == "__main__":
    main()
