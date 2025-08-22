FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Non-root user
RUN useradd -m app && chown -R app:app /app
USER app
# Read-only FS at runtime (enable with Kubernetes)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
