# ml_service/app/main.py
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="finassist-ml")

# Пример простого health-check endpoint
@app.get("/health")
def health():
    return {"status": "ok"}

# Пример predict (скелет)
class PredictRequest(BaseModel):
    text: str

class PredictResponse(BaseModel):
    top3: list

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    # временный заглушечный ответ (пока модель не готова)
    return {"top3": [{"category": "unknown", "confidence": 0.0}]}
