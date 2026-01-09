FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем системные зависимости (включая реализацию netcat)
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
     build-essential \
     gcc \
     libpq-dev \
     curl \
     netcat-openbsd \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/scripts/entrypoint.sh

EXPOSE 8000

CMD ["sh", "-c", "./scripts/entrypoint.sh"]
