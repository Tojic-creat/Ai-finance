#!/usr/bin/env bash
#
# scripts/run_ml_train.sh
#
# Универсальный скрипт для тренировки ML-модели в ml_service.
# Использование:
#   DATA_PATH=/data/train.csv MODEL_NAME=spend_cat MODEL_DIR=ml_service/models ./scripts/run_ml_train.sh
#
# Окружение и опции (по умолчанию можно переопределить):
#  DATA_PATH            - путь до тренировочных данных (CSV или директория). Если не задан, используется синтетический датасет.
#  ML_SERVICE_DIR       - директория ml_service (по умолчанию ml_service)
#  MODEL_DIR            - куда сохранять модель (по умолчанию ml_service/models)
#  MODEL_NAME           - базовое имя модели (по умолчанию model)
#  MODEL_VERSION        - версия модели (если не задана — timestamp будет использован)
#  S3_UPLOAD            - 1/0 — загружать модель в S3/MinIO (по умолчанию 0)
#  S3_BUCKET            - имя бакета для загрузки
#  S3_PREFIX            - префикс в бакете (опционально)
#  S3_ENDPOINT_URL      - endpoint для MinIO (опционально; например http://minio:9000)
#  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY - если используете S3/MinIO
#  MLFLOW_TRACKING_URI  - если задан и mlflow установлен — произойдёт лог модели в MLflow
#  MAX_WAIT_FOR_DATA    - сколько секунд ждать появления DATA_PATH (default 60)
#  VERBOSE              - 1/0 for verbose logs
#
set -euo pipefail

# Defaults
: "${ML_SERVICE_DIR:=ml_service}"
: "${MODEL_DIR:=${ML_SERVICE_DIR}/models}"
: "${MODEL_NAME:=model}"
: "${MODEL_VERSION:=}"
: "${DATA_PATH:=}"
: "${S3_UPLOAD:=0}"
: "${S3_BUCKET:=}"
: "${S3_PREFIX:=models}"
: "${S3_ENDPOINT_URL:=}"
: "${MAX_WAIT_FOR_DATA:=60}"
: "${SLEEP_INTERVAL:=3}"
: "${VERBOSE:=1}"

log() {
  if [ "${VERBOSE}" = "1" ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
  fi
}

err() {
  echo "ERROR: $*" >&2
}

# wait for a file or directory to appear (if DATA_PATH set)
wait_for_data() {
  if [ -z "${DATA_PATH}" ]; then
    log "DATA_PATH not set — пропускаем ожидание данных (будет использован синтетический датасет)."
    return 0
  fi

  local elapsed=0
  log "Ожидание данных по пути '${DATA_PATH}' (макс ${MAX_WAIT_FOR_DATA}s)..."
  while [ $elapsed -lt "${MAX_WAIT_FOR_DATA}" ]; do
    if [ -e "${DATA_PATH}" ]; then
      log "Данные доступны: ${DATA_PATH}"
      return 0
    fi
    sleep "${SLEEP_INTERVAL}"
    elapsed=$((elapsed + SLEEP_INTERVAL))
  done

  err "Данные не появились по пути ${DATA_PATH} после ${MAX_WAIT_FOR_DATA}s"
  return 1
}

# run train.py if present
run_user_train_script() {
  local train_script="${ML_SERVICE_DIR}/train.py"
  if [ -f "${train_script}" ]; then
    log "Найден ${train_script} — запускаем его."
    # Передаём путь к данным и путь сохранения как аргументы
    python "${train_script}" \
      --data "${DATA_PATH:-}" \
      --output-dir "${MODEL_DIR}" \
      --model-name "${MODEL_NAME}" \
      --model-version "${MODEL_VERSION}" || {
        err "train.py завершился с ошибкой"
        return 1
      }
    return 0
  fi
  return 2  # signal: script not found
}

# fallback python trainer: простой sklearn pipeline and save
fallback_train() {
  log "Запуск fallback тренировки (scikit-learn)."
  python - <<PY
import os, json, time
from datetime import datetime
from pathlib import Path

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "${MODEL_DIR}"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DATA_PATH = os.environ.get("DATA_PATH", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "${MODEL_NAME}")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "")

# Simple synthetic dataset if DATA_PATH missing
if DATA_PATH and os.path.exists(DATA_PATH):
    import pandas as pd
    df = pd.read_csv(DATA_PATH)
    # Simple heuristic: if label column exists try to detect, else assume last column is target
    if "target" in df.columns:
        X = df.drop(columns=["target"]).values
        y = df["target"].values
    else:
        # assume last column is target
        X = df.iloc[:, :-1].values
        y = df.iloc[:, -1].values
else:
    from sklearn.datasets import make_regression
    X, y = make_regression(n_samples=1000, n_features=10, noise=0.1, random_state=42)

# simple pipeline
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_squared_error
import joblib
import numpy as np

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

pipe = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler", StandardScaler()),
    ("model", RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1)),
])

start = time.time()
pipe.fit(X_train, y_train)
train_time = time.time() - start

preds = pipe.predict(X_test)
try:
    r2 = float(r2_score(y_test, preds))
    mse = float(mean_squared_error(y_test, preds))
except Exception:
    r2 = None
    mse = None

# versioning: timestamp if not provided
if not MODEL_VERSION:
    MODEL_VERSION = datetime.utcnow().strftime("%Y%m%d%H%M%S")

model_filename = f"{MODEL_NAME}_v{MODEL_VERSION}.joblib"
meta_filename = f"{MODEL_NAME}_v{MODEL_VERSION}.json"

model_path = MODEL_DIR / model_filename
meta_path = MODEL_DIR / meta_filename

joblib.dump(pipe, model_path)

metadata = {
    "model_name": MODEL_NAME,
    "model_version": MODEL_VERSION,
    "train_time_seconds": train_time,
    "r2": r2,
    "mse": mse,
    "timestamp_utc": datetime.utcnow().isoformat(),
    "train_samples": int(X_train.shape[0]),
    "test_samples": int(X_test.shape[0])
}

with open(meta_path, "w") as f:
    json.dump(metadata, f, indent=2)

print("Model saved to:", model_path)
print("Metadata saved to:", meta_path)
print("Metrics:", metadata)
PY

  return $?
}

# upload a file to S3 using boto3 if available
upload_to_s3() {
  if [ "${S3_UPLOAD}" != "1" ]; then
    log "S3_UPLOAD != 1 — пропускаем загрузку в S3/MinIO."
    return 0
  fi

  if [ -z "${S3_BUCKET}" ]; then
    err "S3_UPLOAD=1, но S3_BUCKET не задан."
    return 1
  fi

  # list files produced (the most recent ones) and upload them
  log "Пытаюсь загрузить модели из ${MODEL_DIR} в s3://${S3_BUCKET}/${S3_PREFIX}/"
  python - <<PY
import os, sys
from pathlib import Path
import boto3
from botocore.exceptions import BotoCoreError, ClientError

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "${MODEL_DIR}"))
bucket = os.environ.get("S3_BUCKET")
prefix = os.environ.get("S3_PREFIX", "${S3_PREFIX}")
endpoint = os.environ.get("S3_ENDPOINT_URL", "${S3_ENDPOINT_URL}") or None

if not bucket:
    print("S3_BUCKET not set", file=sys.stderr)
    sys.exit(2)

session = boto3.session.Session()
s3 = session.client(
    "s3",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    endpoint_url=endpoint
)

uploaded = []
for p in sorted(MODEL_DIR.glob("*{}*".format(os.environ.get("MODEL_NAME", "${MODEL_NAME}")))):
    if p.is_file():
        key = f"{prefix}/{p.name}"
        try:
            print(f"Uploading {p} -> s3://{bucket}/{key}")
            s3.upload_file(str(p), bucket, key)
            uploaded.append(key)
        except (BotoCoreError, ClientError) as e:
            print("Upload failed:", e, file=sys.stderr)

if uploaded:
    print("Uploaded keys:", uploaded)
else:
    print("No files uploaded")
PY

  return $?
}

# optionally log to MLflow if available
log_to_mlflow() {
  if [ -z "${MLFLOW_TRACKING_URI:-}" ]; then
    log "MLFLOW_TRACKING_URI not set — пропускаем MLflow logging."
    return 0
  fi

  # try to import mlflow and log the most recent metadata file
  python - <<PY
import os, json
from pathlib import Path
try:
    import mlflow
except Exception as e:
    print("mlflow not available:", e)
    raise SystemExit(0)

mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", "${MODEL_DIR}"))
# find latest metadata json for this model name
meta_files = sorted(MODEL_DIR.glob(f"{os.environ.get('MODEL_NAME','${MODEL_NAME}')}_v*.json"))
if not meta_files:
    print("No metadata files found; skipping MLflow logging")
    raise SystemExit(0)

meta = json.load(open(meta_files[-1]))
model_file_candidates = sorted(MODEL_DIR.glob(f"{os.environ.get('MODEL_NAME','${MODEL_NAME}')}_v*.joblib"))
if not model_file_candidates:
    print("No model files found; skipping MLflow logging")
    raise SystemExit(0)

model_path = str(model_file_candidates[-1])
version = meta.get("model_version", "unknown")
with mlflow.start_run(run_name=f"{os.environ.get('MODEL_NAME','${MODEL_NAME}')}:{version}"):
    # log metrics
    for k in ("r2","mse","train_time_seconds","train_samples","test_samples"):
        if k in meta and meta[k] is not None:
            mlflow.log_metric(k, float(meta[k]))
    # log metadata
    mlflow.log_dict(meta, "metadata.json")
    try:
        # Try to log model (requires mlflow.sklearn)
        mlflow.sklearn.log_model(
            artifact_path="model",
            sk_model=None,
            artifact_file=model_path
        )
    except Exception:
        # fallback: upload artifact file
        mlflow.log_artifact(model_path, artifact_path="model-file")
print("MLflow logging done.")
PY
  return $?
}

# -------------------- Main --------------------
main() {
  log "Starting ML train script"
  log "ML_SERVICE_DIR=${ML_SERVICE_DIR}"
  log "MODEL_DIR=${MODEL_DIR}"
  log "MODEL_NAME=${MODEL_NAME}"
  log "S3_UPLOAD=${S3_UPLOAD}"

  mkdir -p "${MODEL_DIR}"

  # Wait for data (if requested)
  if [ -n "${DATA_PATH}" ]; then
    wait_for_data
  fi

  # If user provided train.py — run it
  run_user_train_script || rc=$?
  if [ "${rc:-0}" = "0" ]; then
    log "train.py completed successfully."
  else
    if [ "${rc:-0}" = "2" ]; then
      log "train.py not found — выполняем fallback тренинг."
      fallback_train
    else
      err "train.py failed with code ${rc} — aborting."
      exit "${rc}"
    fi
  fi

  # If MODEL_VERSION not set, try to find latest in MODEL_DIR and set it for uploads
  if [ -z "${MODEL_VERSION}" ]; then
    # find latest metadata file to infer version
    latest_meta=$(ls -1t "${MODEL_DIR}"/*${MODEL_NAME}_v* 2>/dev/null | head -n 1 || true)
    if [ -n "${latest_meta}" ]; then
      # attempt to extract model version from filename (pattern: name_v<version>.json or .joblib)
      ver=$(basename "${latest_meta}" | sed -E "s/.*_v([0-9]+)[^.]*/\\1/")
      MODEL_VERSION="${ver:-$(date +%Y%m%d%H%M%S)}"
    else
      MODEL_VERSION="$(date +%Y%m%d%H%M%S)"
    fi
  fi
  export MODEL_VERSION

  # Upload to S3/MinIO if requested
  if [ "${S3_UPLOAD}" = "1" ]; then
    upload_to_s3 || log "S3 upload reported errors (see above)."
  fi

  # Log to MLflow if configured
  if [ -n "${MLFLOW_TRACKING_URI:-}" ]; then
    log "Attempting MLflow logging..."
    log_to_mlflow || log "MLflow logging skipped/failed (non-fatal)."
  fi

  log "ML train finished. MODEL_NAME=${MODEL_NAME}, MODEL_VERSION=${MODEL_VERSION}"
}

main "$@"
