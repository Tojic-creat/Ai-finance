# ml_service/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установим системные зависимости, необходимые для некоторых Python пакетов
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       build-essential \
       gcc \
       libpq-dev \
       curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Скопировать только requirements сначала (кэшируем pip layer)
COPY requirements-ml.txt /app/requirements-ml.txt
RUN python -m pip install --upgrade pip \
 && pip install -r /app/requirements-ml.txt

# Скопировать код приложения
COPY . /app

# Порт, на котором будет слушать FastAPI (uvicorn)
EXPOSE 8000

# Команда запуска: uvicorn (в dev режиме, autoreload можно включить отдельно)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
