# backend/Dockerfile
FROM python:3.11-slim

# системные зависимости
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev curl netcat \
  && rm -rf /var/lib/apt/lists/*

# рабочая директория
WORKDIR /app

# копируем файлы зависимостей и установим
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# копируем приложение
COPY . /app

# убедимся, что скрипты имеют права
RUN chmod +x /app/scripts/entrypoint.sh

# порты
EXPOSE 8000

# по умолчанию запуск через entrypoint.sh (в docker-compose указано)
CMD ["sh", "-c", "./scripts/entrypoint.sh"]
