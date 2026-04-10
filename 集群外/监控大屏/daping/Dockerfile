FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DB_PATH=/app/data/alerts.db \
    APP_CONFIG_PATH=/app/config/settings.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/config /app/static/audio

EXPOSE 5000

CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:5000", "app:app"]
