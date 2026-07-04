FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist vote state and logs outside the container
VOLUME ["/app/data"]

ENV PYTHONUNBUFFERED=1

# Default: one vote cycle (for cron/systemd timers)
CMD ["python3", "run.py", "--once", "--config", "config.cloud.yaml"]
